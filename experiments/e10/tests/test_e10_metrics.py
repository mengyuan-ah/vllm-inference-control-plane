from e10.metrics import censored_duration, counter_delta, paired_trace_identity, reconcile


def test_counter_reset_across_generation_is_not_subtracted():
    assert counter_delta({"generation": 1, "value": 99}, {"generation": 2, "value": 3}) is None
    assert counter_delta({"generation": 2, "value": 3}, {"generation": 2, "value": 8}) == 5


def test_observation_censoring():
    assert censored_duration(10, None, 15) == {"seconds": 5, "censored": True}
    assert censored_duration(10, 12, 15) == {"seconds": 2, "censored": False}


def test_trace_identity_and_complete_reconciliation():
    rows = [
        {
            "request_id": "a",
            "scheduled_offset_s": 0,
            "prompt_sha256": "x",
            "max_tokens": 128,
            "seed": 1,
            "outcome": "success_no_retry",
        }
    ]
    assert paired_trace_identity(rows, [dict(rows[0])])
    assert reconcile(rows, 1)["valid"]
    assert not reconcile(rows, 2)["valid"]
