"""Cohen's kappa harness — formal judge validation (Phase 5).

Measures judge-vs-human agreement, corrected for chance, on REAL eval verdicts. It
reuses verdicts already on disk plus the stored eval records for source text — it
makes NO model calls. Every judge reduces to a binary label, so we get one kappa each:

    faithfulness: supported / not (per claim)
    relevance:    relevant  / not (per retrieved paper)
    thoroughness: covered   / not (per gold sub-point)
    abstention:   abstained / not (per answer)

Two steps, so the human labels BLIND (verdict hidden until after scoring):

  1) sample — build a labeling sheet + a separate hidden key:
       backend/venv/bin/python evals/kappa_harness.py sample \\
           --run-id baseline-005 --n-per-label 8 --judges faithfulness abstention

     Edit the sheet: set each "human_label" to true/false. The judge's verdict is
     NOT in the sheet (it's in the key file), so you aren't anchored.

  2) score — join your labels to the key and report kappa per judge + disagreements:
       backend/venv/bin/python evals/kappa_harness.py score \\
           --sheet evals/results/baseline-005.kappa_sheet.jsonl \\
           --key   evals/results/baseline-005.kappa_key.json

Design notes (stated for honesty):
- STRATIFIED sampling: positives dominate (faithfulness ~92% supported), so a random
  sample would be almost all easy positives and kappa would be unstable. We sample up
  to --n-per-label of EACH label per judge, oversampling the rarer (disagreement-prone)
  class.
- ONE human rater → kappa is judge-vs-you, not divine ground truth; ~8-16 items/judge
  gives a directional kappa with a wide CI. This is the lightweight version; the number
  should be reported with that caveat.
"""
import _pathsetup  # noqa: F401  -- backend on path + .env

import argparse
import json
import os
import random

from store import EvalStore

RESULTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")
SEED = 0


def _answer_text(rec):
    msgs = rec.get("messages", [])
    if msgs and msgs[-1].get("role") == "assistant":
        return msgs[-1].get("content", "")
    return ""


def _abstracts(rec):
    return {str(a.get("pmid")): a for a in rec.get("retrieved", []) if a.get("pmid")}


def _src_block(articles):
    return "\n\n".join(
        f"PMID {a.get('pmid')} — {a.get('title','')}\n{(a.get('abstract') or '').strip() or '(no abstract)'}"
        for a in articles
    )


def _items_for_card(card, rec, question):
    """Yield (judge, label_bool, human_prompt) tuples extractable from one card+record."""
    by_pmid = _abstracts(rec)
    answer = _answer_text(rec)

    f = card.get("faithfulness")
    if f:
        for cl in f["claims"]:
            arts = [by_pmid[p] for p in cl["cited_pmids"] if p in by_pmid]
            src = _src_block(arts) if arts else "(cited PMID(s) not in retrieved set)"
            yield ("faithfulness", bool(cl["supported"]),
                   f"CLAIM:\n{cl['claim']}\n\nSOURCE(S):\n{src}\n\nIs the claim supported by the source(s)?")

    r = card.get("relevance")
    if r:
        for j in r["judged"]:
            a = by_pmid.get(j["pmid"], {})
            yield ("relevance", bool(j["relevant"]),
                   f"QUESTION:\n{question}\n\nPAPER:\n{_src_block([a]) if a else j['pmid']}\n\nIs this paper topically relevant to the question?")

    t = card.get("thoroughness")
    if t and t.get("covered"):
        for sp in t["covered"]:
            yield ("thoroughness", bool(sp["covered"]),
                   f"ANSWER:\n{answer}\n\nSUB-POINT: {sp['subpoint']}\n\nDoes the answer cover this sub-point?")

    ab = card.get("abstention")
    if ab:
        yield ("abstention", bool(ab["abstained"]),
               f"ANSWER:\n{answer}\n\nDid the answer ABSTAIN (decline for lack of evidence) rather than make a substantive claim?")


def cmd_sample(args):
    rng = random.Random(SEED)
    store = EvalStore()
    records = {(r["question_id"], int(r.get("repeat", 0))): r for r in store.read_run(args.run_id)}

    checks = args.checks or os.path.join(RESULTS_DIR, f"{args.run_id}.checks.json")
    cards = json.load(open(checks))["cards"]

    import dataset as ds
    questions = {r["question_id"]: r.get("question", "") for r in ds.load()}

    # Collect items per judge, tagged by label.
    pool = {j: {True: [], False: []} for j in args.judges}
    for card in cards:
        rec = records.get((card["question_id"], int(card["repeat"])))
        if not rec:
            continue
        for judge, label, prompt in _items_for_card(card, rec, questions.get(card["question_id"], "")):
            if judge in pool:
                pool[judge][label].append({"judge": judge, "label": label, "prompt": prompt,
                                           "qid": card["question_id"], "repeat": card["repeat"]})

    sheet, key = [], {}
    for judge in args.judges:
        chosen = []
        for label in (True, False):
            items = pool[judge][label]
            rng.shuffle(items)
            chosen += items[: args.n_per_label]
        rng.shuffle(chosen)
        for i, it in enumerate(chosen):
            iid = f"{judge[:5]}-{i:03d}"
            sheet.append({"id": iid, "judge": judge, "prompt": it["prompt"],
                          "qid": it["qid"], "repeat": it["repeat"], "human_label": None})
            key[iid] = it["label"]

    sheet_path = args.out_sheet or os.path.join(RESULTS_DIR, f"{args.run_id}.kappa_sheet.jsonl")
    key_path = args.out_key or os.path.join(RESULTS_DIR, f"{args.run_id}.kappa_key.json")
    os.makedirs(RESULTS_DIR, exist_ok=True)
    with open(sheet_path, "w") as fh:
        for row in sheet:
            fh.write(json.dumps(row) + "\n")
    with open(key_path, "w") as fh:
        json.dump(key, fh, indent=2)

    counts = {j: sum(1 for s in sheet if s["judge"] == j) for j in args.judges}
    print(f"wrote {sheet_path} ({len(sheet)} items: {counts})")
    print(f"wrote {key_path} (hidden judge verdicts — do not peek while labeling)")
    print("\nNext: set each row's \"human_label\" to true/false in the sheet, then run `score`.")


def _kappa(pairs):
    """Cohen's kappa for a list of (human_bool, judge_bool)."""
    n = len(pairs)
    if not n:
        return None, 0
    po = sum(1 for h, j in pairs if h == j) / n
    # marginals
    h_pos = sum(1 for h, _ in pairs if h) / n
    j_pos = sum(1 for _, j in pairs if j) / n
    pe = h_pos * j_pos + (1 - h_pos) * (1 - j_pos)
    kappa = (po - pe) / (1 - pe) if pe != 1 else 1.0
    return {"n": n, "po": round(po, 3), "pe": round(pe, 3), "kappa": round(kappa, 3)}, n


def _band(k):
    if k is None:
        return "n/a"
    for lo, name in [(0.81, "almost perfect"), (0.61, "substantial"), (0.41, "moderate"),
                     (0.21, "fair"), (0.0, "slight"), (-1.0, "poor")]:
        if k >= lo:
            return name
    return "poor"


def cmd_score(args):
    sheet = [json.loads(l) for l in open(args.sheet) if l.strip()]
    key = json.load(open(args.key))

    by_judge = {}
    unlabeled = 0
    for row in sheet:
        hl = row.get("human_label")
        if hl is None:
            unlabeled += 1
            continue
        hb = hl if isinstance(hl, bool) else str(hl).strip().lower() in ("true", "y", "yes", "1")
        jb = bool(key[row["id"]])
        by_judge.setdefault(row["judge"], []).append((row["id"], hb, jb))

    if unlabeled:
        print(f"WARNING: {unlabeled} rows still have human_label=null — they are skipped.\n")

    print(f"{'judge':14s} {'n':>3} {'kappa':>7} {'agree':>7}  band")
    print("-" * 52)
    all_pairs = []
    disagreements = []
    for judge, rows in by_judge.items():
        pairs = [(h, j) for _, h, j in rows]
        all_pairs += pairs
        stats, _ = _kappa(pairs)
        print(f"{judge:14s} {stats['n']:>3} {stats['kappa']:>7} {stats['po']:>7}  {_band(stats['kappa'])}")
        for iid, h, j in rows:
            if h != j:
                disagreements.append((judge, iid, h, j))
    overall, _ = _kappa(all_pairs)
    print("-" * 52)
    print(f"{'OVERALL':14s} {overall['n']:>3} {overall['kappa']:>7} {overall['po']:>7}  {_band(overall['kappa'])}")

    if disagreements:
        print(f"\nDisagreements ({len(disagreements)}) — inspect for judge policy edges:")
        for judge, iid, h, j in disagreements:
            print(f"  [{judge}] {iid}: human={h} judge={j}")


def main():
    ap = argparse.ArgumentParser(description="Cohen's kappa judge-validation harness.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("sample", help="build a blind labeling sheet + hidden key")
    s.add_argument("--run-id", required=True)
    s.add_argument("--checks", default=None)
    s.add_argument("--judges", nargs="+",
                   default=["faithfulness", "relevance", "thoroughness", "abstention"])
    s.add_argument("--n-per-label", type=int, default=8, help="max items per (judge,label)")
    s.add_argument("--out-sheet", default=None)
    s.add_argument("--out-key", default=None)
    s.set_defaults(func=cmd_sample)

    c = sub.add_parser("score", help="compute kappa from a filled-in sheet + key")
    c.add_argument("--sheet", required=True)
    c.add_argument("--key", required=True)
    c.set_defaults(func=cmd_score)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
