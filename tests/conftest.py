from datetime import date

import pytest

from app import market_calendar, twse


@pytest.fixture(autouse=True)
def no_throttle(monkeypatch):
    """測試中關閉外部 API 節流（正式環境 TWSE 1.8s／TPEx 1.0s）。"""
    monkeypatch.setattr(twse, "_TWSE_THROTTLE_SECONDS", 0)
    monkeypatch.setattr(twse, "_TPEX_THROTTLE_SECONDS", 0)


@pytest.fixture(autouse=True)
def fixed_market_calendar(monkeypatch):
    """固定「今天」為平日（2026-07-10 週五）並重設休市日曆快取，讓休市判斷可重現（週末跑測試也不誤判）。"""
    monkeypatch.setattr(market_calendar, "_taipei_today", lambda: date(2026, 7, 10))
    market_calendar._cache.update(fetched_on=None, dates=frozenset())
