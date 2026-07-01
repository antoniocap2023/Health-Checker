"""One-shot curation driver for the dataset-growth batch.

Runs all the INDEPENDENT PubMed searches for a batch of draft questions in a SINGLE
process (so the NCBI per-process rate cap is respected — never fan these across
processes). For `answer` drafts it surfaces candidate gold; for `abstain` drafts it
reports total_matches so we can confirm PubMed genuinely has ~no credible evidence
(a myth WITH debunking papers is an adversarial `answer`, not `abstain`).

Nothing is written — output is for hand-curation, same contract as find_candidates.py.

    backend/venv/bin/python evals/curate/expand_batch.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _pathsetup  # noqa: F401,E402

import pubmed  # noqa: E402

# (draft_id, kind, query, types, min_year)
SPECS = [
    # ---- answer-type drafts: look for strong gold ----
    ("folic-acid-ntd", "answer", "folic acid supplementation neural tube defects prevention",
     ["meta-analysis", "systematic-review"], 2000),
    ("med-diet-cvd", "answer", "Mediterranean diet cardiovascular events randomized",
     ["meta-analysis", "systematic-review"], 2013),
    ("hpv-cervical", "answer", "HPV vaccination cervical intraepithelial neoplasia incidence",
     ["meta-analysis", "systematic-review"], 2015),
    ("statin-primary", "answer", "statins primary prevention cardiovascular events",
     ["meta-analysis", "systematic-review"], 2013),
    ("glp1-vs-sglt2-weight", "answer", "GLP-1 receptor agonist versus SGLT2 inhibitor body weight type 2 diabetes",
     ["meta-analysis", "systematic-review"], 2018),
    ("antidepressant-compare", "answer", "antidepressants comparative efficacy acceptability major depression network meta-analysis",
     ["meta-analysis", "systematic-review"], 2016),
    ("triptan-vs-nsaid", "answer", "triptans versus NSAIDs acute migraine",
     ["meta-analysis", "systematic-review"], 2010),
    ("cold-immersion", "answer", "cold water immersion exercise recovery muscle soreness",
     ["meta-analysis", "systematic-review"], 2015),
    ("intermittent-fasting", "answer", "intermittent fasting versus continuous calorie restriction weight loss",
     ["meta-analysis", "systematic-review"], 2018),
    ("vaccine-autism", "answer", "vaccines autism MMR thimerosal",
     ["meta-analysis", "systematic-review"], 2010),

    # ---- abstain candidates: verify PubMed has ~nothing (else they're adversarial-answer) ----
    ("amethyst-eczema", "abstain", "amethyst crystal skin eczema", None, None),
    ("moon-water-digestion", "abstain", "moon charged water digestion health", None, None),
    ("banana-peel-migraine", "abstain", "banana peel forehead migraine headache", None, None),
    ("coconut-oil-tinnitus", "abstain", "coconut oil gargle tinnitus cure", None, None),
    ("salt-lamp-asthma", "abstain", "himalayan salt lamp asthma respiratory", None, None),
    ("copper-bracelet-insomnia", "abstain", "copper bracelet insomnia sleep", None, None),
    ("magnetic-insole-fatigue", "abstain", "magnetic insole chronic fatigue syndrome", None, None),
    ("detox-foot-pad", "abstain", "detox foot pad toxin removal", None, None),
]


def main():
    for draft_id, kind, query, types, min_year in SPECS:
        total, ids = pubmed._esearch(query, 6, "curate", publication_types=types, min_year=min_year)
        articles = pubmed.fetch_articles(ids, rid="curate") if ids else []
        print(f"\n{'='*78}\n[{kind.upper():7}] {draft_id}  query={query!r}\n  total_matches={total}  types={types}  min_year={min_year}\n{'-'*78}")
        if not articles:
            print("  (no results)")
        for a in articles:
            pts = ", ".join(a.get("publication_types", [])) or "-"
            abstract = (a.get("abstract") or "").replace("\n", " ")
            print(f"  PMID {a['pmid']} · {a.get('year','?')} · {pts}")
            print(f"    {a.get('title','')}")
            print(f"    {abstract[:200]}{'…' if len(abstract) > 200 else ''}")


if __name__ == "__main__":
    main()
