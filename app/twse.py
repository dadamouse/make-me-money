"""TWSE API：收盤價查詢與上市公司對照表。"""
import logging
from datetime import date, timedelta

import httpx

from .parser import parse_twse_close
from .supabase import SupabaseClient

logger = logging.getLogger(__name__)

STOCK_DAY_URL = "https://www.twse.com.tw/exchangeReport/STOCK_DAY"
LISTED_COMPANIES_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"
_SYNC_CHUNK_SIZE = 500

# TWSE 上市公司「產業別」代碼對照（t187ap03_L 的 產業別 欄位）
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

    async def fetch_close(self, stock_no: str) -> dict | None:
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

    async def fetch_listed_companies(self) -> list[dict]:
        response = await self._http.get(LISTED_COMPANIES_URL)
        response.raise_for_status()
        return response.json()


async def sync_stocks(db: SupabaseClient, twse: TwseClient) -> int:
    """把上市公司代號↔簡稱 upsert 進 stocks 表，回傳筆數。"""
    companies = await twse.fetch_listed_companies()
    rows = [
        {
            "stock_no": str(c["公司代號"]).strip(),
            "name": str(c["公司簡稱"]).strip(),
            "industry": INDUSTRY_NAMES.get(str(c.get("產業別", "")).strip()),
        }
        for c in companies
        if c.get("公司代號") and c.get("公司簡稱")
    ]
    if not rows:
        raise RuntimeError("TWSE OpenAPI 回傳空資料")
    for i in range(0, len(rows), _SYNC_CHUNK_SIZE):
        await db.insert(
            "stocks?on_conflict=stock_no",
            rows[i : i + _SYNC_CHUNK_SIZE],
            prefer="resolution=merge-duplicates",
        )
    return len(rows)
