"""盤前推播：08:00 總經快報（美日韓指數/ADR/匯率）、08:30 開盤導航（ADR 隱含價＋昨日台股）。"""
import logging
from datetime import datetime, timedelta, timezone

import httpx

from .deps import Deps
from .parser import format_number, sign_of

logger = logging.getLogger(__name__)

_TAIPEI_TZ = timezone(timedelta(hours=8))
_YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
_YAHOO_HEADERS = {"User-Agent": "Mozilla/5.0"}

MACRO_INDICES = (
    ("道瓊", "^DJI"),
    ("S&P 500", "^GSPC"),
    ("那斯達克", "^IXIC"),
    ("費城半導體", "^SOX"),
    ("日經 225", "^N225"),
    ("韓國 KOSPI", "^KS11"),
)
TSM_ADR_SYMBOL = "TSM"
USDTWD_SYMBOL = "USDTWD=X"
_ADR_SHARES_PER_UNIT = 5  # 1 單位 TSM ADR = 5 股台積電


async def fetch_quote(http: httpx.AsyncClient, symbol: str) -> dict | None:
    """Yahoo v8 chart → {price, prev, pct}；以最近兩個有效收盤計算漲跌。"""
    try:
        response = await http.get(
            _YAHOO_CHART_URL.format(symbol=symbol),
            params={"range": "5d", "interval": "1d"},
            headers=_YAHOO_HEADERS,
        )
        response.raise_for_status()
        result = response.json()["chart"]["result"][0]
        closes = [c for c in result["indicators"]["quote"][0]["close"] if c is not None]
        if len(closes) < 2:
            return None
        price, prev = closes[-1], closes[-2]
        return {"price": price, "prev": prev, "pct": (price - prev) / prev * 100}
    except Exception:
        logger.warning("Yahoo 報價失敗 symbol=%s", symbol, exc_info=True)
        return None


def _quote_line(label: str, quote: dict | None) -> str:
    if not quote:
        return f"{label}：資料暫缺"
    arrow = "🔺" if quote["pct"] >= 0 else "🔻"
    return f"{label} {format_number(quote['price'])}　{arrow}{abs(quote['pct']):.1f}%"


async def build_macro_brief(http: httpx.AsyncClient) -> str:
    now = datetime.now(_TAIPEI_TZ)
    lines = [f"🌅 盤前總經快報（{now.strftime('%m/%d %H:%M')}）", "", "【隔夜國際市場】"]
    for label, symbol in MACRO_INDICES:
        lines.append(_quote_line(label, await fetch_quote(http, symbol)))
    lines.append("")
    lines.append("【台股連動指標】")
    lines.append(_quote_line("台積電 ADR", await fetch_quote(http, TSM_ADR_SYMBOL)))
    fx = await fetch_quote(http, USDTWD_SYMBOL)
    if fx:
        direction = "台幣貶" if fx["pct"] >= 0 else "台幣升"
        lines.append(f"美元/台幣 {fx['price']:.3f}　{'🔺' if fx['pct'] >= 0 else '🔻'}{abs(fx['pct']):.2f}%（{direction}）")
    else:
        lines.append("美元/台幣：資料暫缺")
    return "\n".join(lines)


async def _yesterday_taiwan_summary(deps: Deps) -> list[str]:
    lines = []
    closes = await deps.db.get("daily_closes?stock_no=eq.0050&select=trade_date,close&order=trade_date.desc&limit=2")
    if len(closes) == 2:
        last, prev = float(closes[0]["close"]), float(closes[1]["close"])
        pct = (last - prev) / prev * 100
        lines.append(f"0050 收 {format_number(last)}（{sign_of(pct)}{pct:.1f}%）")
    summary_rows = await deps.db.rpc("market_daily_summary", {})
    if summary_rows:
        summary = summary_rows[0]
        if summary.get("institutional_net") is not None:
            net_lots = float(summary["institutional_net"]) / 1000
            lines.append(f"三大法人 {sign_of(net_lots)}{format_number(round(net_lots))} 張")
        if summary.get("margin_change") is not None:
            change = float(summary["margin_change"])
            lines.append(f"融資增減 {sign_of(change)}{format_number(round(change))} 張")
    return lines


async def build_open_brief(deps: Deps) -> str:
    now = datetime.now(_TAIPEI_TZ)
    lines = [f"📣 開盤前導航（{now.strftime('%m/%d %H:%M')}）", ""]

    adr = await fetch_quote(deps.http, TSM_ADR_SYMBOL)
    fx = await fetch_quote(deps.http, USDTWD_SYMBOL)
    tsmc = await deps.db.get("daily_closes?stock_no=eq.2330&select=close&order=trade_date.desc&limit=1")
    lines.append("【ADR 隱含開盤】")
    if adr and fx and tsmc:
        implied = adr["price"] * fx["price"] / _ADR_SHARES_PER_UNIT
        last_close = float(tsmc[0]["close"])
        premium = (implied - last_close) / last_close * 100
        lines.append(
            f"台積電 ADR {adr['price']:.2f} 美元 × 匯率 {fx['price']:.2f} ÷ {_ADR_SHARES_PER_UNIT}"
            f" ≈ {format_number(implied)} 元"
        )
        lines.append(f"對昨收 {format_number(last_close)}：{sign_of(premium)}{premium:.1f}%（正=偏開高、負=偏開低）")
    else:
        lines.append("ADR 或匯率資料暫缺，無法估算")

    yesterday = await _yesterday_taiwan_summary(deps)
    if yesterday:
        lines.append("")
        lines.append("【昨日台股】")
        lines += yesterday

    today = now.date().isoformat()
    dividends = await deps.db.get(f"dividend_events?ex_date=eq.{today}&order=stock_no&limit=8")
    lines.append("")
    lines.append("【今日除權息】")
    if dividends:
        codes = ",".join(d["stock_no"] for d in dividends)
        names = {s["stock_no"]: s["name"] for s in await deps.db.get(f"stocks?stock_no=in.({codes})&select=stock_no,name")}
        for event in dividends:
            cash = event.get("cash_dividend")
            cash_text = f"，息 {format_number(float(cash))} 元" if cash else ""
            lines.append(f"・{event['stock_no']} {names.get(event['stock_no'], '')}：除{event.get('kind') or ''}{cash_text}")
    else:
        lines.append("今日無除權息")
    lines.append("")
    lines.append("09:00 開盤，祝操作順利 📈")
    return "\n".join(lines)
