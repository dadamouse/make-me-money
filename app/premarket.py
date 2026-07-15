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

_MIS_URL = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp"
_MIS_CODES = "tse_t00.tw|tse_2330.tw"  # 加權指數＋台積電（8:30-9:00 回試撮價）


async def fetch_trial_quotes(http: httpx.AsyncClient) -> dict[str, dict]:
    """TWSE MIS 即時報價；試撮時段（8:30-9:00）z 欄為模擬撮合價。"""
    try:
        response = await http.get(
            _MIS_URL,
            params={"ex_ch": _MIS_CODES, "json": "1", "delay": "0"},
            headers=_YAHOO_HEADERS,
        )
        response.raise_for_status()
        quotes = {}
        for item in response.json().get("msgArray", []):
            def _num(key: str) -> float | None:
                try:
                    return float(item.get(key))
                except (TypeError, ValueError):
                    return None

            quotes[item.get("c")] = {
                "name": item.get("n"),
                "last": _num("z"),
                "prev": _num("y"),
                "time": item.get("t"),
                "date": item.get("d"),  # yyyymmdd，供判斷是否為今日資料
            }
        return quotes
    except Exception:
        logger.warning("MIS 試撮報價失敗", exc_info=True)
        return {}


def _trial_line(label: str, quote: dict | None) -> str | None:
    if not quote or quote["last"] is None or not quote["prev"]:
        return None
    pct = (quote["last"] - quote["prev"]) / quote["prev"] * 100
    return f"{label} {format_number(quote['last'])}（{sign_of(pct)}{pct:.1f}%，{quote.get('time') or ''}）"


def _last_bar_is_today(result: dict) -> bool | None:
    """最後一根有效日線是否為該市場的「今天」；缺 meta/timestamp 時回 None（不知道）。

    用 meta.gmtoffset 換算交易所當地日期，不依賴系統時區資料庫。
    """
    try:
        meta = result.get("meta") or {}
        offset = meta.get("gmtoffset")
        timestamps = result.get("timestamp") or []
        raw_closes = result["indicators"]["quote"][0]["close"]
        last_index = max(i for i, c in enumerate(raw_closes) if c is not None)
        if offset is None or last_index >= len(timestamps):
            return None
        shift = timedelta(seconds=offset)
        bar_date = (datetime.fromtimestamp(timestamps[last_index], timezone.utc) + shift).date()
        market_today = (datetime.now(timezone.utc) + shift).date()
        return bar_date == market_today
    except Exception:
        return None


async def fetch_quote(http: httpx.AsyncClient, symbol: str) -> dict | None:
    """Yahoo v8 chart → {price, prev, pct, is_today}；以最近兩個有效收盤計算漲跌。

    is_today：price 是否為該市場今日行情（False＝昨收，None＝無法判斷）。
    """
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
        return {"price": price, "prev": prev, "pct": (price - prev) / prev * 100, "is_today": _last_bar_is_today(result)}
    except Exception:
        logger.warning("Yahoo 報價失敗 symbol=%s", symbol, exc_info=True)
        return None


async def fetch_close_series(http: httpx.AsyncClient, symbol: str, range_: str = "3mo") -> list[float]:
    """Yahoo v8 chart 日收盤序列（由舊到新，已濾 None）；失敗回空列表。"""
    try:
        response = await http.get(
            _YAHOO_CHART_URL.format(symbol=symbol),
            params={"range": range_, "interval": "1d"},
            headers=_YAHOO_HEADERS,
        )
        response.raise_for_status()
        result = response.json()["chart"]["result"][0]
        return [c for c in result["indicators"]["quote"][0]["close"] if c is not None]
    except Exception:
        logger.warning("Yahoo 序列失敗 symbol=%s", symbol, exc_info=True)
        return []


_UP_ARROW = "🔺"   # 紅＝漲（台股慣例）
_DOWN_ARROW = "🔽"  # 藍＝跌（Unicode 沒有綠色下三角，取對比色）


def _arrow(pct: float) -> str:
    return _UP_ARROW if pct >= 0 else _DOWN_ARROW


def _quote_line(label: str, quote: dict | None) -> str:
    if not quote:
        return f"{label}：資料暫缺"
    return f"{label} {format_number(quote['price'])}　{_arrow(quote['pct'])}{abs(quote['pct']):.1f}%"


def _interpret_macro(quotes: dict[str, dict | None], adr: dict | None, fx: dict | None) -> list[str]:
    """把數字翻成白話：這代表什麼、今天該注意什麼（不含段落標題）。"""
    lines = []
    score = 0

    tech = quotes.get("^SOX") or quotes.get("^IXIC")
    if tech:
        pct = tech["pct"]
        if pct <= -1.5:
            lines.append(f"・美股科技股大跌（費半 {pct:.1f}%）→ 台股電子股今天開盤壓力大")
            score -= 1
        elif pct >= 1.5:
            lines.append(f"・美股科技股大漲（費半 +{pct:.1f}%）→ 對台股電子股是順風")
            score += 1
        else:
            lines.append(f"・美股科技股持平（費半 {sign_of(pct)}{pct:.1f}%）→ 對台股影響中性")

    asia_quotes = [q for q in (quotes.get("^N225"), quotes.get("^KS11")) if q]
    asia = [q["pct"] for q in asia_quotes]
    if asia:
        avg = sum(asia) / len(asia)
        # 資料全是昨收時明講，避免把昨天的行情當成今天的亞洲情緒
        stale = bool(asia_quotes) and all(q.get("is_today") is False for q in asia_quotes)
        subject = "日韓股市昨日" if stale else "日韓股市"
        if avg <= -1.5:
            lines.append(f"・{subject}同步走弱（平均 {avg:.1f}%）→ 亞洲整體風險情緒偏差")
            score -= 1
        elif avg >= 1.5:
            lines.append(f"・{subject}同步走強（平均 +{avg:.1f}%）→ 亞洲情緒偏樂觀")
            score += 1
        else:
            lines.append(f"・{subject}波動不大 → 亞洲情緒平穩")

    if adr:
        pct = adr["pct"]
        if pct <= -1:
            lines.append(f"・台積電 ADR 跌 {abs(pct):.1f}% → 台積電今天大概率開低，拖累大盤")
            score -= 1
        elif pct >= 1:
            lines.append(f"・台積電 ADR 漲 {pct:.1f}% → 台積電今天大概率開高，撐盤")
            score += 1
        else:
            lines.append("・台積電 ADR 變動小 → 開盤方向由其他因素決定")

    if fx:
        if fx["pct"] >= 0.3:
            lines.append("・台幣明顯走貶 → 外資資金偏流出，賣壓可能延續")
            score -= 1
        elif fx["pct"] <= -0.3:
            lines.append("・台幣明顯走升 → 外資資金偏流入，是買盤訊號")
            score += 1

    if score <= -2:
        lines.append("→ 總結：🔴 今日偏空，開盤搶反彈要小心，控制部位")
    elif score >= 2:
        lines.append("→ 總結：🟢 今日偏多，但追高前看一下量能有沒有跟上")
    else:
        lines.append("→ 總結：🟡 多空訊號混雜，觀望為主、看開盤後量價再決定")
    return lines


async def _macro_section(http: httpx.AsyncClient) -> tuple[list[str], list[str]]:
    """國際市場＋台股連動指標區塊。回傳 (行情行, 白話解讀行)。"""
    quotes = {symbol: await fetch_quote(http, symbol) for _, symbol in MACRO_INDICES}
    adr = await fetch_quote(http, TSM_ADR_SYMBOL)
    fx = await fetch_quote(http, USDTWD_SYMBOL)

    lines = ["【隔夜國際市場】"]
    for label, symbol in MACRO_INDICES:
        line = _quote_line(label, quotes[symbol])
        # 日韓與台北同步 8:00 開盤——標註資料時點避免把昨收當今日行情
        if symbol in ("^N225", "^KS11") and quotes[symbol] is not None:
            if quotes[symbol].get("is_today") is True:
                line += "（今日盤中）"
            elif quotes[symbol].get("is_today") is False:
                line += "（昨收）"
        lines.append(line)
    lines.append("＊美股為隔夜收盤；日韓 8:00（台北時間）開盤，行末標示資料時點")
    lines.append("")
    lines.append("【台股連動指標】")
    lines.append(_quote_line("台積電 ADR", adr))
    if fx:
        direction = "台幣貶" if fx["pct"] >= 0 else "台幣升"
        lines.append(f"美元/台幣 {fx['price']:.3f}　{_arrow(fx['pct'])}{abs(fx['pct']):.2f}%（{direction}）")
    else:
        lines.append("美元/台幣：資料暫缺")
    return lines, _interpret_macro(quotes, adr, fx)


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


async def build_open_brief(deps: Deps) -> str | None:
    """盤前導航（總經＋試撮合併版，8:40 前後推播）。

    平日試撮時段應有模擬撮合價，完全沒有＝臨時休市（颱風）或 MIS 異常，回 None 跳過推播。
    """
    now = datetime.now(_TAIPEI_TZ)

    # 注：曾有「ADR 隱含開盤價」換算，因 TSM ADR 存在 15-25% 常態溢價，
    # 絕對換算值無預測意義而移除；方向參考用 ADR 漲跌%＋此處的試撮實價。
    trial = await fetch_trial_quotes(deps.http)
    trial_index = trial.get("t00")
    index_line = _trial_line("加權指數", trial_index)
    tsmc_line = _trial_line("台積電", trial.get("2330"))
    if not index_line and not tsmc_line:
        return None

    macro_lines, macro_notes = await _macro_section(deps.http)
    lines = [f"📣 盤前導航（{now.strftime('%m/%d %H:%M')}）", ""]
    lines += macro_lines
    lines.append("")
    lines.append("【台股試撮（8:30-9:00 模擬撮合）】")
    lines += [line for line in (index_line, tsmc_line) if line]
    lines.append("＊8:55 前試撮可掛假單，價格僅供參考")

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
    lines.append("【📖 白話解讀】")
    lines += macro_notes
    if trial_index and trial_index["last"] is not None and trial_index["prev"]:
        trial_pct = (trial_index["last"] - trial_index["prev"]) / trial_index["prev"] * 100
        if trial_pct <= -1:
            lines.append(f"・試撮指數 {trial_pct:.1f}% → 開盤預告重挫，別急著接刀，等 9:05 後量價站穩再說")
        elif trial_pct >= 1:
            lines.append(f"・試撮指數 +{trial_pct:.1f}% → 開盤偏強，留意是否開高走低")
        else:
            lines.append("・試撮指數接近平盤 → 開盤方向不明，看首 15 分鐘量能")
    summary_rows2 = await deps.db.rpc("market_daily_summary", {})
    if summary_rows2 and summary_rows2[0].get("institutional_net") is not None:
        net = float(summary_rows2[0]["institutional_net"])
        if net < 0:
            lines.append("・昨日法人合計賣超 → 大戶偏保守，反彈先看量")
        else:
            lines.append("・昨日法人合計買超 → 大戶仍在承接，回檔支撐較強")
    if summary_rows2 and summary_rows2[0].get("margin_change") is not None:
        change = float(summary_rows2[0]["margin_change"])
        if change < 0:
            lines.append("・融資減少 → 散戶槓桿退場中，籌碼趨於乾淨（偏正面）")
        elif change > 50000:
            lines.append("・融資大增 → 散戶搶進，追高風險升高（跌時賣壓會被放大）")
    lines.append("")
    lines.append("09:00 開盤，祝操作順利 📈")
    return "\n".join(lines)
