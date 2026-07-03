"""每日快照：收盤價＋成交量、融資融券、三大法人、除權息日，全部 upsert 進 Supabase。"""
import logging
from datetime import datetime, timedelta, timezone

import httpx

from .parser import roc_compact_to_iso
from .supabase import SupabaseClient
from .twse import TwseClient

logger = logging.getLogger(__name__)

_CHUNK_SIZE = 500
_TAIPEI_TZ = timezone(timedelta(hours=8))
_T86_LOOKBACK_DAYS = 4


def _num(raw) -> float | None:
    s = str(raw or "").replace(",", "").strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _diff(today: float | None, previous: float | None) -> float | None:
    if today is None or previous is None:
        return None
    return today - previous


# ---------- 收盤價＋成交量＋開高低 ----------
def _to_close_row(code, date_compact, close_raw, volume_raw, open_raw, high_raw, low_raw) -> dict | None:
    stock_no = str(code or "").strip()
    trade_date = roc_compact_to_iso(date_compact)
    close = _num(close_raw)
    if not stock_no or not trade_date or close is None:
        return None
    return {
        "stock_no": stock_no,
        "trade_date": trade_date,
        "close": close,
        "volume": _num(volume_raw),
        "open": _num(open_raw),
        "high": _num(high_raw),
        "low": _num(low_raw),
    }


def listed_close_rows(quotes: list[dict]) -> list[dict]:
    """TWSE STOCK_DAY_ALL → daily_closes 資料列。"""
    rows = [
        _to_close_row(
            q.get("Code"), q.get("Date"), q.get("ClosingPrice"), q.get("TradeVolume"),
            q.get("OpeningPrice"), q.get("HighestPrice"), q.get("LowestPrice"),
        )
        for q in quotes or []
    ]
    return [row for row in rows if row]


def otc_close_rows(quotes: list[dict]) -> list[dict]:
    """TPEx 每日收盤行情 → daily_closes 資料列。"""
    rows = [
        _to_close_row(
            q.get("SecuritiesCompanyCode"), q.get("Date"), q.get("Close"), q.get("TradingShares"),
            q.get("Open"), q.get("High"), q.get("Low"),
        )
        for q in quotes or []
    ]
    return [row for row in rows if row]


# ---------- 融資融券 ----------
def listed_margin_rows(data: list[dict], trade_date: str | None) -> list[dict]:
    """TWSE MI_MARGN（無日期欄，帶入當日上市交易日）→ daily_margins 資料列。"""
    if not trade_date:
        return []
    rows = []
    for item in data or []:
        stock_no = str(item.get("股票代號", "")).strip()
        if not stock_no:
            continue
        margin_balance = _num(item.get("融資今日餘額"))
        short_balance = _num(item.get("融券今日餘額"))
        rows.append(
            {
                "stock_no": stock_no,
                "trade_date": trade_date,
                "margin_balance": margin_balance,
                "margin_change": _diff(margin_balance, _num(item.get("融資前日餘額"))),
                "short_balance": short_balance,
                "short_change": _diff(short_balance, _num(item.get("融券前日餘額"))),
            }
        )
    return rows


def otc_margin_rows(data: list[dict]) -> list[dict]:
    """TPEx 融資融券餘額 → daily_margins 資料列。"""
    rows = []
    for item in data or []:
        stock_no = str(item.get("SecuritiesCompanyCode", "")).strip()
        trade_date = roc_compact_to_iso(item.get("Date"))
        if not stock_no or not trade_date:
            continue
        margin_balance = _num(item.get("MarginPurchaseBalance"))
        short_balance = _num(item.get("ShortSaleBalance"))
        rows.append(
            {
                "stock_no": stock_no,
                "trade_date": trade_date,
                "margin_balance": margin_balance,
                "margin_change": _diff(margin_balance, _num(item.get("MarginPurchaseBalancePreviousDay"))),
                "short_balance": short_balance,
                "short_change": _diff(short_balance, _num(item.get("ShortSaleBalancePreviousDay"))),
            }
        )
    return rows


# ---------- 三大法人 ----------
def listed_institutional_rows(api_json: dict | None) -> list[dict]:
    """TWSE T86（欄位以 fields 名稱定位）→ daily_institutional 資料列。"""
    if not api_json or api_json.get("stat") != "OK":
        return []
    date_raw = str(api_json.get("date", ""))
    if len(date_raw) != 8:
        return []
    trade_date = f"{date_raw[:4]}-{date_raw[4:6]}-{date_raw[6:8]}"
    index = {name: i for i, name in enumerate(api_json.get("fields") or [])}
    code_i = index.get("證券代號")
    total_i = index.get("三大法人買賣超股數")
    if code_i is None or total_i is None:
        return []
    foreign_i = index.get("外陸資買賣超股數(不含外資自營商)")
    trust_i = index.get("投信買賣超股數")
    dealer_i = index.get("自營商買賣超股數")
    rows = []
    for raw in api_json.get("data", []):
        stock_no = str(raw[code_i]).strip()
        if not stock_no:
            continue
        rows.append(
            {
                "stock_no": stock_no,
                "trade_date": trade_date,
                "foreign_net": _num(raw[foreign_i]) if foreign_i is not None else None,
                "trust_net": _num(raw[trust_i]) if trust_i is not None else None,
                "dealer_net": _num(raw[dealer_i]) if dealer_i is not None else None,
                "total_net": _num(raw[total_i]),
            }
        )
    return rows


def otc_institutional_rows(data: list[dict]) -> list[dict]:
    """TPEx 三大法人買賣明細 → daily_institutional 資料列。"""
    rows = []
    for item in data or []:
        stock_no = str(item.get("SecuritiesCompanyCode", "")).strip()
        trade_date = roc_compact_to_iso(item.get("Date"))
        if not stock_no or not trade_date:
            continue
        rows.append(
            {
                "stock_no": stock_no,
                "trade_date": trade_date,
                "foreign_net": _num(
                    item.get("Foreign Investors include Mainland Area Investors (Foreign Dealers excluded)-Difference")
                ),
                "trust_net": _num(item.get("SecuritiesInvestmentTrustCompanies-Difference")),
                "dealer_net": _num(item.get("Dealers-Difference")),
                "total_net": _num(item.get("TotalDifference")),
            }
        )
    return rows


# ---------- 除權息 ----------
def listed_dividend_rows(data: list[dict]) -> list[dict]:
    """TWSE TWT48U_ALL 除權息預告 → dividend_events 資料列。"""
    rows = []
    for item in data or []:
        stock_no = str(item.get("Code", "")).strip()
        ex_date = roc_compact_to_iso(item.get("Date"))
        if not stock_no or not ex_date:
            continue
        rows.append(
            {
                "stock_no": stock_no,
                "ex_date": ex_date,
                "kind": str(item.get("Exdividend", "")).strip(),
                "cash_dividend": _num(item.get("CashDividend")),
                "stock_dividend_ratio": _num(item.get("StockDividendRatio")),
            }
        )
    return rows


def otc_dividend_rows(data: list[dict]) -> list[dict]:
    """TPEx 除權息預告 → dividend_events 資料列（'除息' 正規化為 '息'）。"""
    rows = []
    for item in data or []:
        stock_no = str(item.get("SecuritiesCompanyCode", "")).strip()
        ex_date = roc_compact_to_iso(item.get("ExRrightsExDividendDate"))
        if not stock_no or not ex_date:
            continue
        rows.append(
            {
                "stock_no": stock_no,
                "ex_date": ex_date,
                "kind": str(item.get("ExRrightsExDividend", "")).strip().replace("除", ""),
                "cash_dividend": _num(item.get("CashDividend")),
                "stock_dividend_ratio": _num(item.get("StockDividendRatio")),
            }
        )
    return rows


# ---------- 主流程 ----------
async def _upsert(db: SupabaseClient, table: str, conflict: str, key_fields: tuple, rows: list[dict]) -> int:
    unique = {tuple(row[k] for k in key_fields): row for row in rows}
    deduped = list(unique.values())
    for i in range(0, len(deduped), _CHUNK_SIZE):
        await db.insert(
            f"{table}?on_conflict={conflict}",
            deduped[i : i + _CHUNK_SIZE],
            prefer="resolution=merge-duplicates",
        )
    return len(deduped)


async def _collect(label: str, coroutine, mapper) -> list[dict]:
    try:
        return mapper(await coroutine)
    except httpx.HTTPError:
        logger.warning("%s 快照來源失敗，跳過", label, exc_info=True)
        return []


async def _collect_listed_institutional(twse: TwseClient) -> list[dict]:
    """T86 需帶日期；從台北今天往回試最多 4 天（假日/尚未公布會查無資料）。"""
    today = datetime.now(_TAIPEI_TZ).date()
    for offset in range(_T86_LOOKBACK_DAYS):
        day = today - timedelta(days=offset)
        try:
            rows = listed_institutional_rows(await twse.fetch_listed_institutional(day.strftime("%Y%m%d")))
        except httpx.HTTPError:
            logger.warning("上市三大法人查詢失敗 date=%s", day, exc_info=True)
            continue
        if rows:
            return rows
    return []


async def run_snapshot(db: SupabaseClient, twse: TwseClient) -> dict:
    """抓兩市場最新資料並 upsert；同日重跑等於覆寫，不會重複。"""
    close_rows = listed_close_rows(await twse.fetch_listed_quotes())
    listed_trade_date = max((row["trade_date"] for row in close_rows), default=None)
    close_rows += await _collect("上櫃收盤", twse.fetch_otc_quotes(), otc_close_rows)

    margin_rows = await _collect(
        "上市融資融券", twse.fetch_listed_margins(), lambda data: listed_margin_rows(data, listed_trade_date)
    )
    margin_rows += await _collect("上櫃融資融券", twse.fetch_otc_margins(), otc_margin_rows)

    institutional_rows = await _collect_listed_institutional(twse)
    institutional_rows += await _collect("上櫃三大法人", twse.fetch_otc_institutional(), otc_institutional_rows)

    dividend_rows = await _collect("上市除權息", twse.fetch_listed_dividends(), listed_dividend_rows)
    dividend_rows += await _collect("上櫃除權息", twse.fetch_otc_dividends(), otc_dividend_rows)

    closes = await _upsert(db, "daily_closes", "stock_no,trade_date", ("stock_no", "trade_date"), close_rows)
    margins = await _upsert(db, "daily_margins", "stock_no,trade_date", ("stock_no", "trade_date"), margin_rows)
    institutional = await _upsert(
        db, "daily_institutional", "stock_no,trade_date", ("stock_no", "trade_date"), institutional_rows
    )
    dividends = await _upsert(db, "dividend_events", "stock_no,ex_date", ("stock_no", "ex_date"), dividend_rows)
    trade_dates = sorted({row["trade_date"] for row in close_rows})
    return {
        "closes": closes,
        "margins": margins,
        "institutional": institutional,
        "dividends": dividends,
        "trade_dates": trade_dates,
    }
