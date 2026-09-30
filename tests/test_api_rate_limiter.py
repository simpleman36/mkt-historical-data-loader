import time

from api_rate_limiter import apply_rate_limits, get_group_stats


def test_nth_plus_one_call_blocks_until_window_frees():
    class Api:
        def call(self):
            return "ok"

    apply_rate_limits(Api, ["call"], group="test-block", limits={1: 3})
    api = Api()

    t0 = time.monotonic()
    for _ in range(3):
        assert api.call() == "ok"
    assert time.monotonic() - t0 < 0.5          # first N calls are not throttled
    assert get_group_stats("test-block")["window_counts"] == {1: 3}

    api.call()                                   # (N+1)th waits for the 1s window
    assert time.monotonic() - t0 >= 0.9
    assert Api.call.get_stats()["calls"] == 4
