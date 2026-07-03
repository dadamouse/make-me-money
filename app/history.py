"""價格歷史：優先讀 daily_closes，不足時從 TWSE/TPEx 回補並存回。"""
import logging
from datetime import date, timedelta

import httpx

from .parser import roc_slash_to_iso
from .supabase import SupabaseClient
from .twse import MARKET_TPEX, TwseClient

logger = logging.getLogger(__name__)

MIN_ROWS = 60          # 低於此天數就觸發回補（MA60 所需）
TARGET_ROWS = 70
MAX_BACKFILL_MONTHS = 5
_READ_LIMIT = 130


def _num(raw) -> float | None:
    s = str(raw or "").replace(",", "").strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _month_row(stock_no: str, raw: list, volume_unit: int) -> dict | None:
    """月資料列 [日期, 成交量, 成交金額, 開盤, 最高, 最低, 收盤, ...] → daily_closes 資料列。"""
    trade_date = roc_slash_to_iso(raw[0])
    close = _num(raw[6])
    if not trade_date or close is None:
        return None
    volume = _num(raw[1])
    return {
        "stock_no": stock_no,
        "trade_date": trade_date,
        "close": close,
        "high": _num(raw[4]),
        "low": _num(raw[5]),
        "volume": volume * volume_unit if volume is not None else None,
    }


def parse_twse_month_rows(api_json: dict | None, stock_no: str) -> list[dict]:
    """TWSE STOCK_DAY（單位：股）。"""
    if not api_json or api_json.get("stat") != "OK":
        return []
    rows = [_month_row(stock_no, raw, volume_unit=1) for raw in api_json.get("data", [])]
    return [row for row in rows if row]


def parse_tpex_month_rows(api_json: dict | None, stock_no: str) -> list[dict]:
    """TPEx tradingStock（成交張數，×1000 轉成股）。"""
    tables = (api_json or {}).get("tables") or []
    data = tables[0].get("data", []) if tables else []
    rows = [_month_row(stock_no, raw, volume_unit=1000) for raw in data]
    return [row for row in rows if row]


def _sufficient(rows: list[dict]) -> bool:
    return len(rows) >= MIN_ROWS and all(r.get("high") is not None for r in rows[-10:])


async def _read(db: SupabaseClient, stock_no: str) -> list[dict]:
    rows = await db.get(
        f"daily_closes?stock_no=eq.{stock_no}&select=trade_date,close,high,low&order=trade_date.desc&limit={_READ_LIMIT}"
    )
    return [
        {**row, "close": _num(row.get("close")), "high": _num(row.get("high")), "low": _num(row.get("low"))}
        for row in reversed(rows)
    ]


async def _backfill(twse: TwseClient, stock_no: str, market: str | None) -> list[dict]:
    collected: dict[str, dict] = {}
    month_start = date.today().replace(day=1)
    for _ in range(MAX_BACKFILL_MONTHS):
        try:
            if market == MARKET_TPEX:
                api = await twse.fetch_otc_month(stock_no, month_start.year, month_start.month)
                rows = parse_tpex_month_rows(api, stock_no)
            else:
                api = await twse.fetch_listed_month(stock_no, month_start.year, month_start.month)
                rows = parse_twse_month_rows(api, stock_no)
        except httpx.HTTPError:
            logger.warning("歷史回補失敗 stock_no=%s month=%s", stock_no, month_start, exc_info=True)
            rows = []
        for row in rows:
            collected[row["trade_date"]] = row
        if len(collected) >= TARGET_ROWS:
            break
        month_start = (month_start - timedelta(days=1)).replace(day=1)
    return sorted(collected.values(), key=lambda row: row["trade_date"])


async def get_price_history(db: SupabaseClient, twse: TwseClient, stock_no: str, market: str | None) -> list[dict]:
    """回傳由舊到新的 {trade_date, close, high, low}；不足 60 天時自動回補並寫回 daily_closes。"""
    rows = await _read(db, stock_no)
    if _sufficient(rows):
        return rows
    fetched = await _backfill(twse, stock_no, market)
    if fetched:
        await db.insert("daily_closes?on_conflict=stock_no,trade_date", fetched, prefer="resolution=merge-duplicates")
        rows = await _read(db, stock_no)
    return rows
