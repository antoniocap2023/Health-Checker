"""Pure aggregation tests (aggregate.py) on synthetic scorecards. No network."""
import aggregate as A


def ans(qid, repeat=0, split="dev", ok=True, fabricated_rate=0.0, recall=1.0,
        hit=True, n_cited=2, n_sup=2, uncited_rate=0.0, coverage=1.0, abst_correct=True):
    rate = (n_sup / n_cited) if n_cited else None
    return {
        "question_id": qid, "repeat": repeat, "type": "factual", "split": split,
        "expected_behavior": "answer",
        "validity": {"ok": ok, "fabricated_rate": fabricated_rate,
                     "fabricated_pmids": [] if ok else ["999"], "n_cited": n_cited},
        "relevance": {"recall": recall, "hit": hit, "gold": [], "retrieved": [], "hits": []},
        "faithfulness": {"n_cited_claims": n_cited, "n_supported": n_sup,
                         "faithfulness_rate": rate, "uncited_rate": uncited_rate,
                         "n_claims": n_cited, "claims": [], "uncited_claims": []},
        "thoroughness": {"coverage": coverage, "covered": []},
        "abstention": {"correct": abst_correct, "abstained": False},
    }


def absr(qid, repeat=0, split="dev", correct=True):
    return {
        "question_id": qid, "repeat": repeat, "type": "adversarial", "split": split,
        "expected_behavior": "abstain",
        "validity": {"ok": True, "fabricated_rate": 0.0, "fabricated_pmids": [], "n_cited": 0},
        "relevance": None, "faithfulness": None, "thoroughness": None,
        "abstention": {"correct": correct, "abstained": correct},
    }


def test_faithfulness_is_claim_weighted():
    cards = [ans("q1", n_cited=2, n_sup=1), ans("q2", n_cited=3, n_sup=3)]
    m = A.aggregate(cards)["by_split"]["all"]["metrics"]
    # claim-weighted = (1+3)/(2+3) = 0.8 (NOT the mean-of-rates 0.75)
    assert m["faithfulness_rate"] == 0.8


def test_relevance_recall_mean():
    cards = [ans("q1", recall=1.0), ans("q2", recall=0.5)]
    assert A.aggregate(cards)["by_split"]["all"]["metrics"]["relevance_recall"] == 0.75


def test_conditional_excludes_retrieval_failed():
    cards = [
        ans("q1", hit=True, n_cited=2, n_sup=0),    # retrieval ok, unfaithful
        ans("q2", hit=False, n_cited=2, n_sup=2),   # retrieval FAILED, would-be faithful
    ]
    split = A.aggregate(cards)["by_split"]["all"]
    assert split["metrics"]["faithfulness_rate"] == 0.5          # raw: (0+2)/(2+2)
    assert split["conditional"]["faithfulness_rate_given_retrieval_ok"] == 0.0  # only q1 counts


def test_attribution_buckets():
    cards = [
        ans("q1", ok=False, fabricated_rate=0.5),               # -> validity
        ans("q2", hit=False),                                   # -> relevance
        ans("q3", n_cited=2, n_sup=1),                          # -> faithfulness
        ans("q4", coverage=0.5),                                # -> thoroughness
        ans("q5"),                                              # -> ok
        absr("q6", correct=False),                              # -> abstention
        absr("q7", correct=True),                               # -> ok
    ]
    a = A.aggregate(cards)["by_split"]["all"]["attribution"]
    assert a == {"ok": 2, "validity": 1, "relevance": 1,
                 "faithfulness": 1, "thoroughness": 1, "abstention": 1}


def test_noise_stdev_across_repeat_slices():
    cards = [
        ans("q1", repeat=0, recall=1.0), ans("q2", repeat=0, recall=1.0),  # slice 0 -> 1.0
        ans("q1", repeat=1, recall=0.0), ans("q2", repeat=1, recall=0.0),  # slice 1 -> 0.0
    ]
    noise = A.aggregate(cards)["by_split"]["all"]["noise"]["relevance_recall"]
    assert noise["mean"] == 0.5
    assert noise["stdev"] == 0.5   # pstdev([1.0, 0.0])
    assert noise["n_slices"] == 2


def test_split_separation():
    cards = [ans("q1", split="dev"), ans("q2", split="test"), absr("q3", split="test")]
    by = A.aggregate(cards)["by_split"]
    assert by["dev"]["n_records"] == 1
    assert by["test"]["n_records"] == 2
    assert by["all"]["n_records"] == 3
