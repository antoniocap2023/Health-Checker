"""Online eval — score REAL user conversations with the reference-free checks.

Points the checks that need NO gold — validity, relevance (topical), faithfulness, and
the abstention *outcome* — at a sample of the most-recent real conversations from a
conversations table (dev or prod). Reports + persists reference-free metrics plus
flagged problem conversations, to monitor actual usage (not just the gold benchmark).
READ-ONLY: it only scans/reads the conversations table.

    backend/venv/bin/python evals/online_eval.py --source dev  --limit 25
    backend/venv/bin/python evals/online_eval.py --source prod --limit 50 --batch   # run in background

Scope (reference-free): thoroughness, relevance gold-recall, and abstention correctness
need gold and are NOT computed here. Because evidence is stored per conversation
(last-write-wins), online scoring reflects the conversation's LATEST answered turn.
"""
import _pathsetup  # noqa: F401  -- backend on path + backend/.env

import argparse
import json
import logging
import os
from collections import Counter
from datetime import datetime
from statistics import mean

from anthropic import Anthropic

import scorecard
from judges import abstention, decompose, faithfulness, relevance_judge
from judges.client import build_request, judge, run_batch
from scorecard import assemble_online
from storage import ConversationStore

RESULTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")
TABLE_FMT = "health-checker-conversations-{}"


def _mean(vals):
    vals = [v for v in vals if v is not None]
    return round(mean(vals), 3) if vals else None


def _scorable(rec):
    """True if the record's latest turn is a substantive assistant answer we can score.
    Skips messages-only items (pre-stream save, disconnects, turns awaiting a reply)."""
    msgs = rec.get("messages") or []
    return bool(msgs) and msgs[-1].get("role") == "assistant" and bool((msgs[-1].get("content") or "").strip())


def load_recent(table, limit, max_scan, log):
    """Sample the most-recent real conversations from `table` (read-only)."""
    store = ConversationStore(table_name=table)
    raw = store.scan_sample(only_real=True, max_scan=max_scan)
    scorable = [r for r in raw if _scorable(r)]
    scorable.sort(key=lambda r: r.get("created_at") or "", reverse=True)
    chosen = scorable[:limit]
    log.info("scanned=%d scorable=%d scoring=%d (table=%s)", len(raw), len(scorable), len(chosen), table)
    return chosen, {"scanned": len(raw), "scorable": len(scorable), "scored": len(chosen)}


# ---- scoring: build per-record `parts`, then scorecard.assemble_online ----

def _safe_judge(client, system, user, schema, **kw):
    """Sequential judge call that degrades to None (assemble_online tolerates it)."""
    try:
        return judge(client, system, user, schema, **kw)
    except Exception as exc:  # noqa: BLE001
        logging.getLogger("healthchecker.eval.online").warning("judge failed: %s", exc)
        return None


def _parts_sequential(client, rec):
    answer = scorecard._answer_text(rec)
    parts = {"abstention": _safe_judge(client, *abstention.build(answer), abstention.SCHEMA)}
    retrieved = rec.get("retrieved", [])
    if retrieved:
        parts["decompose"] = _safe_judge(client, *decompose.build(answer), decompose.SCHEMA,
                                         max_tokens=decompose.MAX_TOKENS)
        q = scorecard._question_text(rec)
        parts["relevance"] = [_safe_judge(client, *relevance_judge.build_paper(q, [], a),
                                          relevance_judge.SCHEMA) for a in retrieved]
        plan = faithfulness.plan(rec, decompose.parse(parts["decompose"]))
        parts["faithfulness"] = [_safe_judge(client, *faithfulness.build_claim(e["claim"], e["articles"]),
                                             faithfulness.SCHEMA) for e in plan["to_judge"]]
    return parts


def score_sequential(records, client, log):
    cards = []
    for rec in records:
        log.info("scoring %s", rec.get("conversation_id"))
        cards.append(assemble_online(rec, _parts_sequential(client, rec)))
    return cards


def score_batched(records, client, log):
    """Two-phase Batches-API scoring (faithfulness depends on decompose). Async ~1h."""
    reqs1 = []
    for i, rec in enumerate(records):
        answer = scorecard._answer_text(rec)
        reqs1.append(build_request(f"{i}_abst", *abstention.build(answer), abstention.SCHEMA))
        if rec.get("retrieved"):
            reqs1.append(build_request(f"{i}_dec", *decompose.build(answer), decompose.SCHEMA,
                                       max_tokens=decompose.MAX_TOKENS))
            q = scorecard._question_text(rec)
            for j, a in enumerate(rec["retrieved"]):
                reqs1.append(build_request(f"{i}_rel_{j}", *relevance_judge.build_paper(q, [], a),
                                           relevance_judge.SCHEMA))
    log.info("phase 1 batch: %d requests", len(reqs1))
    res1 = run_batch(client, reqs1, log=log)

    reqs2, plans = [], {}
    for i, rec in enumerate(records):
        if not rec.get("retrieved"):
            continue
        plan = faithfulness.plan(rec, decompose.parse(res1.get(f"{i}_dec")))
        plans[i] = plan
        for k, e in enumerate(plan["to_judge"]):
            reqs2.append(build_request(f"{i}_faith_{k}", *faithfulness.build_claim(e["claim"], e["articles"]),
                                       faithfulness.SCHEMA))
    log.info("phase 2 batch: %d requests", len(reqs2))
    res2 = run_batch(client, reqs2, log=log)

    cards = []
    for i, rec in enumerate(records):
        parts = {"abstention": res1.get(f"{i}_abst")}
        if rec.get("retrieved"):
            parts["decompose"] = res1.get(f"{i}_dec")
            parts["relevance"] = [res1.get(f"{i}_rel_{j}") for j in range(len(rec["retrieved"]))]
            parts["faithfulness"] = [res2.get(f"{i}_faith_{k}") for k in range(len(plans[i]["to_judge"]))]
        cards.append(assemble_online(rec, parts))
    return cards


# ---- summary + flags (reference-free) ----

def summarize(cards):
    withret = [c for c in cards if c.get("faithfulness")]
    verifiable = sum(c["faithfulness"]["n_verifiable"] for c in withret)
    supported = sum(c["faithfulness"]["n_supported"] for c in withret)
    return {
        "n_scored": len(cards),
        "n_with_retrieval": len(withret),
        "validity_ok_rate": _mean([1.0 if c["validity"]["ok"] else 0.0 for c in cards]),
        "fabricated_pmid_rate": _mean([c["validity"]["fabricated_rate"] for c in cards]),
        "relevance_hit_rate": _mean([1.0 if c["relevance"]["hit"] else 0.0 for c in withret if c.get("relevance")]),
        "relevance_precision": _mean([c["relevance"]["precision"] for c in withret if c.get("relevance")]),
        "faithfulness_rate": (supported / verifiable) if verifiable else None,
        "unverifiable_citation_rate": _mean([c["faithfulness"]["unverifiable_rate"] for c in withret]),
        "uncited_claim_rate": _mean([c["faithfulness"]["uncited_rate"] for c in withret]),
        "abstention_outcomes": dict(Counter(c["abstention"]["outcome"] for c in cards)),
    }


def flag(cards):
    """Per-conversation problems worth a human look: fabricated citations or unsupported
    cited claims (the reference-free failure modes that matter on real traffic)."""
    flags = []
    for c in cards:
        issues = []
        fab = c["validity"]["fabricated_pmids"]
        if fab:
            issues.append(f"fabricated PMIDs cited (not retrieved): {fab}")
        f = c.get("faithfulness")
        if f:
            for claim in f["claims"]:
                if not claim["supported"]:
                    issues.append(f"unsupported claim: {claim['claim'][:120]}")
        if issues:
            flags.append({"conversation_id": c["conversation_id"], "question": (c["question"] or "")[:120],
                          "issues": issues})
    return flags


def main():
    ap = argparse.ArgumentParser(
        description="Score real user conversations with the reference-free checks (READ-ONLY). "
                    "--batch uses the Batches API (async ~1h) — run it in the background.")
    ap.add_argument("--source", choices=["dev", "prod"], help="which conversations table (dev/prod)")
    ap.add_argument("--table", default=None, help="explicit table name (overrides --source)")
    ap.add_argument("--limit", type=int, default=25, help="how many recent conversations to score")
    ap.add_argument("--max-scan", type=int, default=2000, help="cap items read from the table (cost bound)")
    ap.add_argument("--batch", action="store_true", help="score via Batches API (async; run in background)")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    if not args.table and not args.source:
        ap.error("give --source dev|prod or --table")
    table = args.table or TABLE_FMT.format(args.source)
    env = args.source or table

    logging.basicConfig(level="WARNING", format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    log = logging.getLogger("healthchecker.eval.online")
    log.setLevel("INFO")

    records, counts = load_recent(table, args.limit, args.max_scan, log)
    if not records:
        print(f"no scorable conversations in {table} (scanned {counts['scanned']}).")
        return

    client = Anthropic()
    cards = score_batched(records, client, log) if args.batch else score_sequential(records, client, log)

    summary = summarize(cards)
    flags = flag(cards)

    os.makedirs(RESULTS_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = args.out or os.path.join(RESULTS_DIR, f"online-{env}-{stamp}.json")
    with open(out, "w") as fh:
        json.dump({"env": env, "table": table, "counts": counts,
                   "summary": summary, "flags": flags, "cards": cards}, fh, indent=2, default=str)

    print(f"\nwrote {out}")
    print(f"online eval — {env} (scanned {counts['scanned']}, scored {counts['scored']}):")
    for k, v in summary.items():
        print(f"  {k}: {v}")
    if flags:
        print(f"\nflagged {len(flags)} conversation(s) for review:")
        for fl in flags:
            print(f"  [{fl['conversation_id']}] {fl['question']}")
            for iss in fl["issues"]:
                print(f"      - {iss}")


if __name__ == "__main__":
    main()
