"""market_calendar 單元測試：週末、休市日曆 API 失敗 fail-open。"""
import asyncio
from datetime import date

import httpx

from app import market_calendar


def test_weekend_is_closed(monkeypatch):
    monkeypatch.setattr(market_calendar, "_taipei_today", lambda: date(2026, 7, 11))  # 週六
    assert asyncio.run(market_calendar.is_scheduled_closed_today(None)) is True


class _BoomHttp:
    async def get(self, url):
        raise httpx.ConnectError("boom")


def test_holiday_api_failure_fails_open():
    """API 失敗視為交易日（寧可多發也不漏發）；conftest 已固定今天為平日。"""
    assert asyncio.run(market_calendar.is_scheduled_closed_today(_BoomHttp())) is False
