"""主力買賣超：MoneyDJ 券商分點頁（zco）擷取前 15 大買賣超彙總，僅同步持股清單。"""
import asyncio
import logging
import re
from html import unescape

from .deps import Deps
from .parser import format_number, sign_of

logger = logging.getLogger(__name__)

ZCO_URL = "https://fubon-ebrokerdj.fbs.com.tw/z/zc/zco/zco_{stock_no}.djhtm"
_HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
_FETCH_INTERVAL_SECONDS = 2.0  # 溫和抓取：每檔間隔 2 秒
_ROW_PATTERN = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S | re.I)
_CELL_PATTERN = re.compile(r"<td[^>]*>(.*?)</td>", re.S | re.I)
_DATE_PATTERN = re.compile(r"最後更新日：(\d{4})/(\d{2})/(\d{2})")


def _num(raw: str) -> float | None:
    s = raw.replace(",", "").replace("%", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


def parse_zco(html_text: str, stock_no: str) -> dict | None:
    """zco 頁 → 前15大買賣超彙總。表列結構：買超側 5 欄＋賣超側 5 欄。"""
    date_match = _DATE_PATTERN.search(html_text)
    if not date_match:
        return None
    trade_date = "-".join(date_match.groups())

    top_buy = top_sell = 0.0
    buy_pct = sell_pct = 0.0
    found = False
    for row_html in _ROW_PATTERN.findall(html_text):
        cells = [unescape(re.sub(r"<[^>]+>", "", cell)).strip() for cell in _CELL_PATTERN.findall(row_html)]
        if len(cells) != 10 or "券商" in cells[0]:
            continue
        buy_net, buy_ratio = _num(cells[3]), _num(cells[4])
        sell_net, sell_ratio = _num(cells[8]), _num(cells[9])
        if buy_net is None and sell_net is None:
            continue
        found = True
        top_buy += buy_net or 0.0
        buy_pct += buy_ratio or 0.0
        top_sell += sell_net or 0.0
        sell_pct += sell_ratio or 0.0
    if not found:
        return None
    return {
        "stock_no": stock_no,
        "trade_date": trade_date,
        "top_buy_lots": top_buy,
        "top_sell_lots": top_sell,
        "net_lots": top_buy - top_sell,
        "concentration_pct": round(buy_pct - sell_pct, 2),
    }


async def sync_broker_flows(deps: Deps) -> dict:
    """抓所有成員持股的主力買賣超（每檔一頁、間隔 2 秒），upsert 冪等。

    fubon 站的 TLS 憑證缺 Subject Key Identifier，OpenSSL 3 會拒連（2026-07-08 起），
    故用關閉驗證的專用連線抓（僅公開展示資料）。
    """
    holdings = await deps.db.get("holdings?select=stock_no")
    codes = sorted({str(row["stock_no"]) for row in holdings})
    rows = []
    scraper = deps.scrape_http or deps.http
    for i, code in enumerate(codes):
        if i:
            await asyncio.sleep(_FETCH_INTERVAL_SECONDS)
        try:
            response = await scraper.get(ZCO_URL.format(stock_no=code), headers=_HEADERS, timeout=30)
            response.raise_for_status()
            response.encoding = "cp950"
            parsed = parse_zco(response.text, code)
            if parsed:
                rows.append(parsed)
        except Exception:
            logger.warning("主力買賣超擷取失敗 stock_no=%s", code, exc_info=True)
    if rows:
        await deps.db.insert(
            "daily_broker_flows?on_conflict=stock_no,trade_date",
            rows,
            prefer="resolution=merge-duplicates",
        )
    return {"stocks": len(codes), "rows": len(rows)}


def broker_flow_text(flow: dict | None) -> str | None:
    """顯示用：主力 +2,340 張（集中 4.5%）。"""
    if not flow or flow.get("net_lots") is None:
        return None
    net = float(flow["net_lots"])
    text = f"主力 {sign_of(net)}{format_number(round(net))} 張"
    if flow.get("concentration_pct") is not None:
        text += f"（集中 {sign_of(float(flow['concentration_pct']))}{float(flow['concentration_pct']):.1f}%）"
    return text
