"""台股休市判斷：週末＋TWSE 休市日曆（國定假日）。

颱風等臨時休市不在日曆內，須另以「試撮無資料」（盤前）或
「當日無新收盤資料」（盤後）判斷，見 premarket.build_open_brief 與 main.daily_picks。
"""
import logging
from datetime import date, datetime, timedelta, timezone

import httpx

from .parser import roc_compact_to_iso

logger = logging.getLogger(__name__)

_TAIPEI_TZ = timezone(timedelta(hours=8))
_HOLIDAY_URL = "https://openapi.twse.com.tw/v1/holidaySchedule/holidaySchedule"
# 休市日曆一天只抓一次；API 失敗時沿用舊快取（fail open：寧可多發也不漏發）
_cache: dict = {"fetched_on": None, "dates": frozenset()}


def _taipei_today() -> date:
    return datetime.now(_TAIPEI_TZ).date()


def taipei_today_iso() -> str:
    """台北時區的今天（ISO 字串），供各排程比對資料新鮮度。"""
    return _taipei_today().isoformat()


async def _fetch_holiday_dates(http: httpx.AsyncClient) -> frozenset[str]:
    today = taipei_today_iso()
    if _cache["fetched_on"] == today:
        return _cache["dates"]
    try:
        response = await http.get(_HOLIDAY_URL)
        response.raise_for_status()
        dates = frozenset(
            iso for item in response.json() if (iso := roc_compact_to_iso(str(item.get("Date", ""))))
        )
        _cache.update(fetched_on=today, dates=dates)
        return dates
    except Exception:
        logger.warning("TWSE 休市日曆查詢失敗，沿用快取", exc_info=True)
        return _cache["dates"]


async def is_scheduled_closed_today(http: httpx.AsyncClient) -> bool:
    """今天是否為週末或國定休市日（不含颱風等臨時休市）。"""
    today = _taipei_today()
    if today.weekday() >= 5:
        return True
    return today.isoformat() in await _fetch_holiday_dates(http)
