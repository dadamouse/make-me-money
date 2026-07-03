"""每日收盤快照：把上市＋上櫃全部股票的收盤價存進 daily_closes 表。"""
import logging

import httpx

from .parser import roc_compact_to_iso
from .supabase import SupabaseClient
from .twse import TwseClient

logger = logging.getLogger(__name__)

_CHUNK_SIZE = 500


def _to_close_row(code: str, date_compact: str, close_raw: str) -> dict | None:
    stock_no = str(code or "").strip()
    trade_date = roc_compact_to_iso(date_compact)
    try:
        close = float(str(close_raw or "").replace(",", ""))
    except ValueError:
        return None
    if not stock_no or not trade_date:
        return None
    return {"stock_no": stock_no, "trade_date": trade_date, "close": close}


def listed_close_rows(quotes: list[dict]) -> list[dict]:
    """TWSE STOCK_DAY_ALL → daily_closes 資料列。"""
    rows = [_to_close_row(q.get("Code"), q.get("Date"), q.get("ClosingPrice")) for q in quotes or []]
    return [row for row in rows if row]


def otc_close_rows(quotes: list[dict]) -> list[dict]:
    """TPEx 每日收盤行情 → daily_closes 資料列。"""
    rows = [_to_close_row(q.get("SecuritiesCompanyCode"), q.get("Date"), q.get("Close")) for q in quotes or []]
    return [row for row in rows if row]


async def snapshot_daily_closes(db: SupabaseClient, twse: TwseClient) -> dict:
    """抓兩市場最新收盤並 upsert；同日重跑等於覆寫，不會重複。"""
    rows = listed_close_rows(await twse.fetch_listed_quotes())
    try:
        rows += otc_close_rows(await twse.fetch_otc_quotes())
    except httpx.HTTPError:
        logger.warning("TPEx 收盤快照失敗，僅存上市資料", exc_info=True)
    unique = {(row["stock_no"], row["trade_date"]): row for row in rows}
    deduped = list(unique.values())
    for i in range(0, len(deduped), _CHUNK_SIZE):
        await db.insert(
            "daily_closes?on_conflict=stock_no,trade_date",
            deduped[i : i + _CHUNK_SIZE],
            prefer="resolution=merge-duplicates",
        )
    trade_dates = sorted({row["trade_date"] for row in deduped})
    return {"rows": len(deduped), "trade_dates": trade_dates}
