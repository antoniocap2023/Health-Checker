"""Pure aggregation tests (aggregate.py) on synthetic scorecards. No network."""
import aggregate as A


def ans(qid, repeat=0, split="dev", ok=True, fabricated_rate=0.0, hit=True, precision=1.0,
        n_verifiable=2, n_sup=2, n_unverifiable=0, uncited_rate=0.0, coverage=1.0,
        abst_correct=True, gold_recall=1.0):
    rate = (n_sup / n_verifiable) if n_verifiable else None
    n_cited = n_verifiable + n_unverifiable
    return {
        "question_id": qid, "repeat": repeat, "type": "factual", "split": split,
        "expected_behavior": "answer",
        "validity": {"ok": ok, "fabricated_rate": fabricated_rate,
                     "fabricated_pmids": [] if ok else ["999"], "n_cited": n_cited},
        "relevance": {"hit": hit, "precision": precision, "n_retrieved": 3,
                      "n_relevant": round(precision * 3) if precision is not None else 0,
                      "judged": [], "gold_recall": gold_recall, "gold_hit": hit, "gold_hits": []},
        "faithfulness": {"claims": [], "unverifiable_claims": [], "uncited_claims": [],
                         "n_claims": n_cited, "n_cited_claims": n_cited, "n_verifiable": n_verifiable,
                         "n_supported": n_sup, "n_unverifiable": n_unverifiable,
                         "faithfulness_rate": rate,
                         "unverifiable_rate": (n_unverifiable / n_cited) if n_cited else 0.0,
                         "uncited_rate": uncited_rate},
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


def test_faithfulness_is_claim_weighted_over_verifiable():
    cards = [ans("q1", n_verifiable=2, n_sup=1), ans("q2", n_verifiable=3, n_sup=3)]
    m = A.aggregate(cards)["by_split"]["all"]["metrics"]
    assert m["faithfulness_rate"] == 0.8   # (1+3)/(2+3)


def test_unverifiable_excluded_from_faithfulness_but_reported():
    # one verifiable+supported claim, one unverifiable claim
    cards = [ans("q1", n_verifiable=1, n_sup=1, n_unverifiable=1)]
    m = A.aggregate(cards)["by_split"]["all"]["metrics"]
    assert m["faithfulness_rate"] == 1.0                 # unverifiable doesn't drag it down
    assert m["unverifiable_citation_rate"] == 0.5        # 1 of 2 cited claims


def test_relevance_hit_and_precision_means():
    cards = [ans("q1", hit=True, precision=1.0), ans("q2", hit=False, precision=0.0)]
    m = A.aggregate(cards)["by_split"]["all"]["metrics"]
    assert m["relevance_hit_rate"] == 0.5
    assert m["relevance_precision"] == 0.5


def test_conditional_excludes_retrieval_failed():
    cards = [
        ans("q1", hit=True, n_verifiable=2, n_sup=0),    # retrieval ok, unfaithful
        ans("q2", hit=False, n_verifiable=2, n_sup=2),   # retrieval FAILED, would-be faithful
    ]
    split = A.aggregate(cards)["by_split"]["all"]
    assert split["metrics"]["faithfulness_rate"] == 0.5            # raw (0+2)/(2+2)
    assert split["conditional"]["faithfulness_rate_given_retrieval_ok"] == 0.0  # only q1


def test_attribution_buckets():
    cards = [
        ans("q1", ok=False, fabricated_rate=0.5),               # -> validity
        ans("q2", hit=False),                                   # -> relevance
        ans("q3", n_verifiable=2, n_sup=1),                     # -> faithfulness
        ans("q4", coverage=0.5),                                # -> thoroughness
        ans("q5"),                                              # -> ok
        absr("q6", correct=False),                              # -> abstention
        absr("q7", correct=True),                               # -> ok
    ]
    a = A.aggregate(cards)["by_split"]["all"]["attribution"]
    assert a == {"ok": 2, "validity": 1, "relevance": 1,
                 "faithfulness": 1, "thoroughness": 1, "abstention": 1}


def test_all_unverifiable_record_is_not_a_faithfulness_failure():
    # a record whose only cited claim is unverifiable should bucket 'ok', not 'faithfulness'
    cards = [ans("q1", n_verifiable=0, n_sup=0, n_unverifiable=2)]
    a = A.aggregate(cards)["by_split"]["all"]["attribution"]
    assert a["faithfulness"] == 0 and a["ok"] == 1


def test_noise_stdev_across_repeat_slices():
    cards = [
        ans("q1", repeat=0, precision=1.0), ans("q2", repeat=0, precision=1.0),  # slice 0 -> 1.0
        ans("q1", repeat=1, precision=0.0), ans("q2", repeat=1, precision=0.0),  # slice 1 -> 0.0
    ]
    noise = A.aggregate(cards)["by_split"]["all"]["noise"]["relevance_precision"]
    assert noise["mean"] == 0.5 and noise["stdev"] == 0.5 and noise["n_slices"] == 2


def test_split_separation():
    cards = [ans("q1", split="dev"), ans("q2", split="test"), absr("q3", split="test")]
    by = A.aggregate(cards)["by_split"]
    assert by["dev"]["n_records"] == 1
    assert by["test"]["n_records"] == 2
    assert by["all"]["n_records"] == 3
