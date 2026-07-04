"""TWSE（上市）與 TPEx（上櫃）API：收盤價查詢與公司對照表同步。"""
import asyncio
import logging
import time
from datetime import date, timedelta

import httpx

from .parser import parse_tpex_close, parse_twse_close
from .supabase import SupabaseClient

logger = logging.getLogger(__name__)

STOCK_DAY_URL = "https://www.twse.com.tw/exchangeReport/STOCK_DAY"
STOCK_DAY_ALL_URL = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
LISTED_COMPANIES_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"
LISTED_MARGINS_URL = "https://openapi.twse.com.tw/v1/exchangeReport/MI_MARGN"
LISTED_DIVIDENDS_URL = "https://openapi.twse.com.tw/v1/exchangeReport/TWT48U_ALL"
LISTED_NEWS_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap04_L"
LISTED_INSTITUTIONAL_URL = "https://www.twse.com.tw/rwd/zh/fund/T86"
TPEX_COMPANIES_URL = "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O"
TPEX_QUOTES_URL = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes"
TPEX_MARGINS_URL = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_margin_balance"
TPEX_DIVIDENDS_URL = "https://www.tpex.org.tw/openapi/v1/tpex_exright_prepost"
TPEX_NEWS_URL = "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap04_O"
TPEX_INSTITUTIONAL_URL = "https://www.tpex.org.tw/openapi/v1/tpex_3insti_daily_trading"
TPEX_STOCK_MONTH_URL = "https://www.tpex.org.tw/www/zh-tw/afterTrading/tradingStock"

MARKET_TWSE = "上市"
MARKET_TPEX = "上櫃"
ETF_INDUSTRY = "ETF"
_ETF_CODE_PREFIX = "00"

_SYNC_CHUNK_SIZE = 500
_TPEX_QUOTES_TTL_SECONDS = 600
_TWSE_THROTTLE_SECONDS = 1.8  # www.twse.com.tw 限制約 3 req/5s，保守間隔避免被鎖 IP
_TPEX_THROTTLE_SECONDS = 1.0


class _Throttle:
    """串行化並強制最小間隔的節流器（對嚴格限流的網域用）。"""

    def __init__(self):
        self._lock = asyncio.Lock()
        self._last = 0.0

    async def wait(self, interval: float) -> None:
        async with self._lock:
            delay = interval - (time.monotonic() - self._last)
            if delay > 0:
                await asyncio.sleep(delay)
            self._last = time.monotonic()

# 「產業別」代碼對照（TWSE t187ap03_L 與 TPEx mopsfin_t187ap03_O 共用同一套代碼）
INDUSTRY_NAMES = {
    "01": "水泥工業", "02": "食品工業", "03": "塑膠工業", "04": "紡織纖維",
    "05": "電機機械", "06": "電器電纜", "08": "玻璃陶瓷", "09": "造紙工業",
    "10": "鋼鐵工業", "11": "橡膠工業", "12": "汽車工業", "14": "建材營造",
    "15": "航運業", "16": "觀光餐旅", "17": "金融保險", "18": "貿易百貨",
    "19": "綜合", "20": "其他", "21": "化學工業", "22": "生技醫療",
    "23": "油電燃氣", "24": "半導體", "25": "電腦及週邊設備", "26": "光電",
    "27": "通信網路", "28": "電子零組件", "29": "電子通路", "30": "資訊服務",
    "31": "其他電子", "32": "文化創意", "33": "農業科技", "34": "電子商務",
    "35": "綠能環保", "36": "數位雲端", "37": "運動休閒", "38": "居家生活",
}


class TwseClient:
    def __init__(self, http: httpx.AsyncClient):
        self._http = http
        self._tpex_quotes_cache: tuple[float, list] | None = None
        self._twse_throttle = _Throttle()
        self._tpex_throttle = _Throttle()

    async def fetch_close(self, stock_no: str, market: str | None = None) -> dict | None:
        """依市場別查收盤價；市場未知（如 ETF）時先試上市、再試上櫃。"""
        if market == MARKET_TPEX:
            return await self._tpex_close(stock_no)
        quote = await self._twse_close(stock_no)
        if quote is None and market != MARKET_TWSE:
            quote = await self._tpex_close(stock_no)
        return quote

    async def _twse_close(self, stock_no: str) -> dict | None:
        """以當月任一天查整月資料取最新收盤；月初無資料時 fallback 上個月。"""
        month_start = date.today().replace(day=1)
        for _ in range(2):
            try:
                await self._twse_throttle.wait(_TWSE_THROTTLE_SECONDS)
                response = await self._http.get(
                    STOCK_DAY_URL,
                    params={"response": "json", "date": month_start.strftime("%Y%m%d"), "stockNo": stock_no},
                )
                response.raise_for_status()
                parsed = parse_twse_close(response.json())
                if parsed:
                    return parsed
            except httpx.HTTPError:
                logger.warning("TWSE 查詢失敗 stock_no=%s month=%s", stock_no, month_start, exc_info=True)
            month_start = (month_start - timedelta(days=1)).replace(day=1)
        return None

    async def _tpex_close(self, stock_no: str) -> dict | None:
        try:
            quotes = await self._tpex_quotes()
        except httpx.HTTPError:
            logger.warning("TPEx 收盤行情查詢失敗", exc_info=True)
            return None
        return parse_tpex_close(quotes, stock_no)

    async def _tpex_quotes(self) -> list:
        """TPEx 收盤行情為全市場清單，以短暫快取避免同一批查詢重複下載。"""
        now = time.monotonic()
        if self._tpex_quotes_cache and now - self._tpex_quotes_cache[0] < _TPEX_QUOTES_TTL_SECONDS:
            return self._tpex_quotes_cache[1]
        response = await self._http.get(TPEX_QUOTES_URL)
        response.raise_for_status()
        quotes = response.json()
        self._tpex_quotes_cache = (now, quotes)
        return quotes

    async def fetch_listed_companies(self) -> list[dict]:
        response = await self._http.get(LISTED_COMPANIES_URL)
        response.raise_for_status()
        return response.json()

    async def fetch_otc_companies(self) -> list[dict]:
        response = await self._http.get(TPEX_COMPANIES_URL)
        response.raise_for_status()
        return response.json()

    async def fetch_listed_quotes(self) -> list[dict]:
        """TWSE 全市場日成交（STOCK_DAY_ALL），含 ETF 的代號與名稱。"""
        response = await self._http.get(STOCK_DAY_ALL_URL)
        response.raise_for_status()
        return response.json()

    async def fetch_otc_quotes(self) -> list:
        return await self._tpex_quotes()

    async def fetch_listed_margins(self) -> list[dict]:
        return await self._get_json(LISTED_MARGINS_URL)

    async def fetch_otc_margins(self) -> list[dict]:
        return await self._get_json(TPEX_MARGINS_URL)

    async def fetch_listed_dividends(self) -> list[dict]:
        return await self._get_json(LISTED_DIVIDENDS_URL)

    async def fetch_otc_dividends(self) -> list[dict]:
        return await self._get_json(TPEX_DIVIDENDS_URL)

    async def fetch_listed_news(self) -> list[dict]:
        return await self._get_json(LISTED_NEWS_URL)

    async def fetch_listed_institutional(self, yyyymmdd: str) -> dict:
        """TWSE T86 三大法人買賣超（指定日期，非交易日回 stat != OK）。"""
        await self._twse_throttle.wait(_TWSE_THROTTLE_SECONDS)
        response = await self._http.get(
            LISTED_INSTITUTIONAL_URL,
            params={"date": yyyymmdd, "selectType": "ALLBUT0999", "response": "json"},
        )
        response.raise_for_status()
        return response.json()

    async def fetch_otc_institutional(self) -> list[dict]:
        return await self._get_json(TPEX_INSTITUTIONAL_URL)

    async def fetch_otc_news(self) -> list[dict]:
        return await self._get_json(TPEX_NEWS_URL)

    async def fetch_listed_month(self, stock_no: str, year: int, month: int) -> dict:
        """TWSE STOCK_DAY 單檔整月（含開高低收與成交量）。"""
        await self._twse_throttle.wait(_TWSE_THROTTLE_SECONDS)
        response = await self._http.get(
            STOCK_DAY_URL,
            params={"response": "json", "date": f"{year}{month:02d}01", "stockNo": stock_no},
        )
        response.raise_for_status()
        return response.json()

    async def fetch_otc_month(self, stock_no: str, year: int, month: int) -> dict:
        """TPEx tradingStock 單檔整月。"""
        await self._tpex_throttle.wait(_TPEX_THROTTLE_SECONDS)
        response = await self._http.get(
            TPEX_STOCK_MONTH_URL,
            params={"code": stock_no, "date": f"{year}/{month:02d}/01", "response": "json"},
        )
        response.raise_for_status()
        return response.json()

    async def _get_json(self, url: str) -> list[dict]:
        response = await self._http.get(url)
        response.raise_for_status()
        return response.json()


def _listed_rows(companies: list[dict]) -> list[dict]:
    return [
        {
            "stock_no": str(c["公司代號"]).strip(),
            "name": str(c["公司簡稱"]).strip(),
            "industry": INDUSTRY_NAMES.get(str(c.get("產業別", "")).strip()),
            "market": MARKET_TWSE,
        }
        for c in companies
        if c.get("公司代號") and c.get("公司簡稱")
    ]


def _otc_rows(companies: list[dict]) -> list[dict]:
    return [
        {
            "stock_no": str(c["SecuritiesCompanyCode"]).strip(),
            "name": str(c["CompanyAbbreviation"]).strip(),
            "industry": INDUSTRY_NAMES.get(str(c.get("SecuritiesIndustryCode", "")).strip()),
            "market": MARKET_TPEX,
        }
        for c in companies
        if c.get("SecuritiesCompanyCode") and c.get("CompanyAbbreviation")
    ]


def _listed_etf_rows(quotes: list[dict]) -> list[dict]:
    return [
        {
            "stock_no": str(q["Code"]).strip(),
            "name": str(q["Name"]).strip(),
            "industry": ETF_INDUSTRY,
            "market": MARKET_TWSE,
        }
        for q in quotes
        if str(q.get("Code", "")).startswith(_ETF_CODE_PREFIX) and q.get("Name")
    ]


def _otc_etf_rows(quotes: list[dict]) -> list[dict]:
    return [
        {
            "stock_no": str(q["SecuritiesCompanyCode"]).strip(),
            "name": str(q["CompanyName"]).strip(),
            "industry": ETF_INDUSTRY,
            "market": MARKET_TPEX,
        }
        for q in quotes
        if str(q.get("SecuritiesCompanyCode", "")).startswith(_ETF_CODE_PREFIX) and q.get("CompanyName")
    ]


async def sync_stocks(db: SupabaseClient, twse: TwseClient) -> int:
    """把上市＋上櫃公司與 ETF 對照 upsert 進 stocks 表，回傳筆數；次要來源失敗時仍同步其餘。"""
    rows = _listed_rows(await twse.fetch_listed_companies())
    if not rows:
        raise RuntimeError("TWSE OpenAPI 回傳空資料")
    for label, loader in (
        ("上櫃公司", lambda: _rows_from(twse.fetch_otc_companies(), _otc_rows)),
        ("上市 ETF", lambda: _rows_from(twse.fetch_listed_quotes(), _listed_etf_rows)),
        ("上櫃 ETF", lambda: _rows_from(twse.fetch_otc_quotes(), _otc_etf_rows)),
    ):
        try:
            rows += await loader()
        except httpx.HTTPError:
            logger.warning("%s 同步失敗，跳過此來源", label, exc_info=True)
    # 依 stock_no 去重（公司基本資料優先，upsert 同批不可有重複 key）
    unique: dict[str, dict] = {}
    for row in rows:
        unique.setdefault(row["stock_no"], row)
    deduped = list(unique.values())
    for i in range(0, len(deduped), _SYNC_CHUNK_SIZE):
        await db.insert(
            "stocks?on_conflict=stock_no",
            deduped[i : i + _SYNC_CHUNK_SIZE],
            prefer="resolution=merge-duplicates",
        )
    return len(deduped)


async def _rows_from(fetch_coroutine, mapper) -> list[dict]:
    return mapper(await fetch_coroutine)
