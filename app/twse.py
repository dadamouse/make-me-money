"""TWSE（上市）與 TPEx（上櫃）API：收盤價查詢與公司對照表同步。"""
import logging
import time
from datetime import date, timedelta

import httpx

from .parser import parse_tpex_close, parse_twse_close
from .supabase import SupabaseClient

logger = logging.getLogger(__name__)

STOCK_DAY_URL = "https://www.twse.com.tw/exchangeReport/STOCK_DAY"
LISTED_COMPANIES_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"
TPEX_COMPANIES_URL = "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O"
TPEX_QUOTES_URL = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes"

MARKET_TWSE = "上市"
MARKET_TPEX = "上櫃"

_SYNC_CHUNK_SIZE = 500
_TPEX_QUOTES_TTL_SECONDS = 600

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


async def sync_stocks(db: SupabaseClient, twse: TwseClient) -> int:
    """把上市＋上櫃公司對照 upsert 進 stocks 表，回傳筆數；上櫃來源失敗時仍同步上市。"""
    rows = _listed_rows(await twse.fetch_listed_companies())
    if not rows:
        raise RuntimeError("TWSE OpenAPI 回傳空資料")
    try:
        rows += _otc_rows(await twse.fetch_otc_companies())
    except httpx.HTTPError:
        logger.warning("TPEx 公司資料同步失敗，僅同步上市公司", exc_info=True)
    for i in range(0, len(rows), _SYNC_CHUNK_SIZE):
        await db.insert(
            "stocks?on_conflict=stock_no",
            rows[i : i + _SYNC_CHUNK_SIZE],
            prefer="resolution=merge-duplicates",
        )
    return len(rows)
