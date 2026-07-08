from app.history import merge_realtime_bar

HISTORY = [
    {"trade_date": "2026-07-06", "close": 2460.0, "open": 2450.0, "high": 2470.0, "low": 2440.0, "volume": 100},
]

BAR = {"trade_date": "2026-07-08", "close": 2440.0, "open": 2450.0, "high": 2470.0, "low": 2430.0, "volume": 25000000}


def test_merge_appends_newer_bar():
    merged = merge_realtime_bar(HISTORY, BAR)
    assert len(merged) == 2
    assert merged[-1]["trade_date"] == "2026-07-08"
    assert merged[-1]["close"] == 2440.0
    assert HISTORY[-1]["trade_date"] == "2026-07-06"  # 原列表不被改動


def test_merge_overwrites_same_day():
    same_day = {**BAR, "trade_date": "2026-07-06", "close": 2455.0, "open": None}
    merged = merge_realtime_bar(HISTORY, same_day)
    assert len(merged) == 1
    assert merged[-1]["close"] == 2455.0
    assert merged[-1]["open"] == 2450.0  # None 不覆蓋既有值


def test_merge_ignores_stale_or_invalid():
    assert merge_realtime_bar(HISTORY, {**BAR, "trade_date": "2026-07-01"}) == HISTORY
    assert merge_realtime_bar(HISTORY, {**BAR, "close": None}) == HISTORY
    assert merge_realtime_bar(HISTORY, None) == HISTORY
    assert merge_realtime_bar([], BAR) == []
