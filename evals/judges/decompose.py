"""Decompose an answer into atomic claims, each with the PMIDs it cited.

Shared step for faithfulness (are claims supported?) and the uncited-claim metric.
Grounded by the inline `(PMID: ...)` markers the agent is prompted to write.
"""
from judges.client import judge

_SYSTEM = """You break a medical answer into atomic, individually-checkable claims.

Rules:
- A claim is a single factual/medical assertion that could be checked against a \
source (e.g. "low-dose aspirin reduces preeclampsia risk"). Split compound \
sentences into separate claims.
- For each claim, list the PubMed IDs cited for it — the digits from inline \
"(PMID: 12345678)" markers in the SAME sentence/clause as the claim. If a claim has \
no citation near it, return an empty list for it.
- IGNORE non-claims: greetings, meta-commentary ("I'll search PubMed..."), hedges \
with no factual content, and pure restatements of the question.
- Return the claims in the order they appear."""

_SCHEMA = {
    "type": "object",
    "properties": {
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "claim": {"type": "string", "description": "the atomic claim text"},
                    "cited_pmids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "PMIDs cited for this claim (digits only), or empty",
                    },
                },
                "required": ["claim", "cited_pmids"],
            },
        }
    },
    "required": ["claims"],
}


def decompose(client, answer):
    """Return a list of {"claim": str, "cited_pmids": [str]} for the answer."""
    result = judge(client, _SYSTEM, f"ANSWER:\n{answer}", _SCHEMA)
    claims = result.get("claims", [])
    # Normalize PMIDs to strings of digits.
    for c in claims:
        c["cited_pmids"] = [str(p) for p in c.get("cited_pmids", []) if str(p).isdigit()]
    return claims
