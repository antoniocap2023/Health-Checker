"""Relevance — does the agent's retrieved set actually address the question?

Reference-free (topical): we judge each *retrieved* paper for whether it provides
evidence relevant to the question, instead of matching against an exhaustive gold
list (which can never be complete — the agent legitimately finds valid alternatives).
Headline metrics: `hit` (retrieved >=1 relevant paper) and `precision` (fraction of
retrieved that are relevant). The exact gold-PMID overlap is kept separately as a
diagnostic (see checks/relevance.py).
"""
from judges.client import judge

_SYSTEM = """You decide whether a PubMed paper is RELEVANT to a medical question — \
i.e. whether it provides evidence that helps answer it.

- Relevant = the paper directly addresses the question's topic and what it asks \
(the intervention/exposure and the outcome/condition in the question). A paper that \
debunks or finds no effect is still relevant if it's about the right topic.
- Not relevant = off-topic, or only tangentially related (different population, \
different intervention, or a passing mention).
- Judge topical relevance only — not the study's quality or size.
- You may see only a title (no abstract); judge from the title in that case.
- Give a one-sentence reason."""

SCHEMA = {
    "type": "object",
    "properties": {
        "relevant": {"type": "boolean"},
        "reasoning": {"type": "string"},
    },
    "required": ["relevant", "reasoning"],
}


def build_paper(question, subpoints, article):
    """The (system, user) prompt pair for judging one retrieved paper's relevance."""
    rubric = ""
    if subpoints:
        rubric = "\nThe question is really asking about: " + "; ".join(subpoints)
    paper = (f"PMID {article.get('pmid')} — {article.get('title','')}\n"
             f"{article.get('abstract','') or '(no abstract available)'}")
    return _SYSTEM, f"QUESTION:\n{question}{rubric}\n\nPAPER:\n{paper}"


def parse_paper(inp):
    """Turn a relevance tool-result into {relevant:bool, reasoning:str}."""
    inp = inp or {}
    return {"relevant": bool(inp.get("relevant")), "reasoning": inp.get("reasoning", "")}


def assemble(judged):
    """Build the relevance block from a list of {pmid, relevant, reasoning} verdicts."""
    n = len(judged)
    n_rel = sum(1 for j in judged if j["relevant"])
    return {
        "judged": judged,
        "n_retrieved": n,
        "n_relevant": n_rel,
        "precision": (n_rel / n) if n else None,
        "hit": n_rel >= 1,
    }


def _judge_paper(client, question, subpoints, article):
    """Sync: judge one paper, returning {relevant, reasoning}. (judge_trust uses this.)"""
    system, user = build_paper(question, subpoints, article)
    return parse_paper(judge(client, system, user, SCHEMA))


def score(client, question, subpoints, retrieved):
    """Judge each retrieved paper for topical relevance. Returns the relevance block."""
    judged = []
    for a in retrieved:
        v = _judge_paper(client, question, subpoints, a)
        judged.append({"pmid": str(a.get("pmid")), **v})
    return assemble(judged)
