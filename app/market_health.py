"""大盤體檢：只陳列事實供判讀，不做預測（A 方案）。"""
import logging
from datetime import datetime, timedelta, timezone

from .chart import render_index_png
from .deps import Deps
from .indicators import rsi
from .market_calendar import taipei_today_iso
from .parser import format_number, sign_of
from .premarket import USDTWD_SYMBOL, fetch_close_series, fetch_quote, fetch_trial_quotes

logger = logging.getLogger(__name__)

_TAIPEI_TZ = timezone(timedelta(hours=8))
_VIX_SYMBOL = "^VIX"


async def _index_series_with_realtime(deps: Deps) -> tuple[list[dict], bool]:
    """market_series（新到舊）；快照尚無今日資料時，用 MIS 即時加權指數補一筆今日暫定值。

    回傳 (series_desc, 是否含即時點)。MIS 資料日期非今天（休市）或抓不到時不補。
    """
    series_desc = await deps.db.rpc("market_series", {"n": 60})
    today = taipei_today_iso()
    if series_desc and str(series_desc[0]["trade_date"]) == today:
        return series_desc, False
    quotes = await fetch_trial_quotes(deps.http)
    t00 = quotes.get("t00") or {}
    raw_date = str(t00.get("date") or "")
    mis_date = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:]}" if len(raw_date) == 8 else None
    if t00.get("last") and mis_date == today:
        return [{"trade_date": today, "taiex": t00["last"], "amount": None}, *series_desc], True
    return series_desc, False


def _position_text(close: float, ma: float | None, label: str) -> str | None:
    if ma is None:
        return None
    bias = (close - ma) / ma * 100
    side = "上方" if bias >= 0 else "下方"
    return f"{label}{side} {abs(bias):.1f}%"


def _streak(values: list[float]) -> tuple[int, str]:
    """由新到舊的日淨額 → 連續同向天數與方向文字。"""
    if not values:
        return 0, ""
    positive = values[0] > 0
    count = 0
    for value in values:
        if (value > 0) == positive and value != 0:
            count += 1
        else:
            break
    return count, "買超" if positive else "賣超"


async def market_regime_warning(deps: Deps) -> str | None:
    """大盤收月線下時的環境警語（選股卡用）；資料不足或在月線上回 None。"""
    try:
        series_desc, _ = await _index_series_with_realtime(deps)
        closes = [float(row["taiex"]) for row in reversed(series_desc)]
        if len(closes) >= 20 and closes[-1] < sum(closes[-20:]) / 20:
            return "⚠️ 大盤收月線下（環境偏空）：突破/強勢型策略勝率下降，名單僅供觀察，新倉宜小。"
    except Exception:
        logger.warning("環境警語計算失敗", exc_info=True)
    return None


async def build_market_health(deps: Deps) -> str:
    lines = []
    notes = []  # 白話解讀（描述現況，不做預測）
    series_desc, realtime = await _index_series_with_realtime(deps)
    closes_asc = [float(row["taiex"]) for row in reversed(series_desc)]
    amounts_asc = [float(row["amount"]) for row in reversed(series_desc) if row.get("amount") is not None]

    if len(closes_asc) >= 2:
        latest, prev = closes_asc[-1], closes_asc[-2]
        pct = (latest - prev) / prev * 100
        date_label = str(series_desc[0]["trade_date"])[5:].replace("-", "/")
        lines.append(f"📋 大盤體檢（{date_label}{' 即時' if realtime else ''}）")
        lines.append(f"加權指數 {format_number(latest)}（{sign_of(pct)}{pct:.1f}%）")
        ma20 = sum(closes_asc[-20:]) / 20 if len(closes_asc) >= 20 else None
        ma60 = sum(closes_asc[-60:]) / 60 if len(closes_asc) >= 60 else None
        positions = [p for p in (_position_text(latest, ma20, "月線"), _position_text(latest, ma60, "季線")) if p]
        if positions:
            lines.append(f"位置：{'｜'.join(positions)}")
        if ma20 is not None:
            if latest < ma20:
                notes.append("・指數收在月線之下 → 短線趨勢偏弱，反彈先看月線能不能站回")
            else:
                notes.append("・指數守在月線之上 → 短線結構還沒壞")
        if len(amounts_asc) >= 6:
            ratio = amounts_asc[-1] / (sum(amounts_asc[-6:-1]) / 5)
            # 即時模式下成交金額還沒進快照，量能是前一交易日的
            lines.append(f"量能{'（前一交易日）' if realtime else ''}：5 日均量的 {ratio:.2f} 倍")
            if not realtime:
                if ratio >= 1.3:
                    notes.append("・帶量 → 今天的方向有大資金參與，行情可信度較高")
                elif ratio <= 0.7:
                    notes.append("・量縮 → 觀望氣氛濃，今天的漲跌參考價值較低")
        rsi_value = rsi(closes_asc, 14)
        if rsi_value is not None:
            lines.append(f"大盤 RSI14：{rsi_value:.0f}")
            if rsi_value >= 70:
                notes.append("・RSI 過熱區 → 市場漲多，隨時可能技術性回檔")
            elif rsi_value <= 30:
                notes.append("・RSI 超賣區 → 市場跌深，但超賣可以更超賣，等止穩訊號")
    else:
        lines.append("📋 大盤體檢")
        lines.append("指數資料累積中")

    # 台幣趨勢：外資資金流向的即時代理指標（USDTWD 升＝台幣貶＝資金偏流出）
    fx_series = await fetch_close_series(deps.http, USDTWD_SYMBOL)
    if len(fx_series) >= 21:
        latest = fx_series[-1]
        day_pct = (latest - fx_series[-2]) / fx_series[-2] * 100
        five_pct = (latest - fx_series[-6]) / fx_series[-6] * 100
        ma20 = sum(fx_series[-20:]) / 20
        ma_bias = (latest - ma20) / ma20 * 100
        trend = "貶值趨勢" if ma_bias > 0 else "升值趨勢"
        lines.append(
            f"匯率：美元/台幣 {latest:.2f}（今日 {sign_of(day_pct)}{day_pct:.2f}%｜5日 {sign_of(five_pct)}{five_pct:.2f}%）"
        )
        lines.append(f"台幣趨勢：月線{'上' if ma_bias > 0 else '下'}方 {abs(ma_bias):.1f}%（{trend}）")
        window = fx_series[-60:]
        if latest >= max(window):
            notes.append("・台幣貶破近 3 個月低點 → 外資匯出訊號強，權值股賣壓要留意")
        elif latest <= min(window):
            notes.append("・台幣升破近 3 個月高點 → 外資大幅匯入，權值股較有支撐")
        elif five_pct >= 0.5:
            notes.append("・台幣近 5 日明顯走貶 → 外資資金偏匯出，對台股是逆風")
        elif five_pct <= -0.5:
            notes.append("・台幣近 5 日明顯走升 → 外資資金偏流入，對台股是助力")
        else:
            notes.append(f"・台幣處{trend}（vs 月線）→ 中期資金流向偏{'出' if ma_bias > 0 else '入'}，短線持平")
    else:
        fx = await fetch_quote(deps.http, USDTWD_SYMBOL)
        if fx:
            direction = "台幣貶" if fx["pct"] >= 0 else "台幣升"
            lines.append(f"匯率：美元/台幣 {fx['price']:.2f}（{sign_of(fx['pct'])}{fx['pct']:.2f}%，{direction}）")

    flows = await deps.db.rpc("market_flow_series", {"n": 10})
    insti_rows = [row for row in flows if row.get("insti_net") is not None]
    insti_values = [float(row["insti_net"]) for row in insti_rows]
    if insti_values:
        days, direction = _streak(insti_values)
        # 法人買賣超盤後才公布：資料不是今天的就標日期，不寫「今日」
        insti_date = str(insti_rows[0].get("trade_date") or "")
        day_label = "今日" if insti_date == taipei_today_iso() else insti_date[5:].replace("-", "/")
        lines.append(
            f"法人：{day_label} {sign_of(insti_values[0])}{format_number(round(insti_values[0] / 1000))} 張（連 {days} 日{direction}）"
        )
        if direction == "賣超":
            notes.append(f"・法人連 {days} 日賣超 → 大戶偏保守，反彈缺大買盤")
        else:
            notes.append(f"・法人連 {days} 日買超 → 大戶仍在承接，回檔有支撐")
    margin_rows = [row for row in flows if row.get("margin_chg") is not None]
    if margin_rows:
        latest_chg = float(margin_rows[0]["margin_chg"])
        five_day = sum(float(row["margin_chg"]) for row in margin_rows[:5])
        margin_date = str(margin_rows[0]["trade_date"])[5:].replace("-", "/")
        lines.append(
            f"融資：{sign_of(latest_chg)}{format_number(round(latest_chg))} 張"
            f"｜5 日累計 {sign_of(five_day)}{format_number(round(five_day))} 張（資料日 {margin_date}）"
        )
        if five_day >= 100_000:
            notes.append("・融資 5 日大增 → 散戶槓桿升溫，市場變脆弱（跌時賣壓會被放大）")
        elif five_day <= -100_000:
            notes.append("・融資 5 日大減 → 槓桿退場、籌碼沉澱中（中期偏正面）")

    breadth_rows = await deps.db.rpc("market_breadth", {})
    if breadth_rows:
        breadth = breadth_rows[0]
        lines.append(
            f"寬度：漲 {format_number(breadth['up_count'])} 家／跌 {format_number(breadth['down_count'])} 家"
            f"｜創20日新高 {breadth['new_high']}／新低 {breadth['new_low']}"
        )
        up, down = float(breadth["up_count"]), float(breadth["down_count"])
        if down > up * 1.5:
            notes.append("・跌多漲少 → 弱勢是全面性的，不是少數權值股拖累")
        if float(breadth["new_low"]) > float(breadth["new_high"]):
            notes.append("・創新低家數多於新高 → 破底股在擴散，選股難度高")

    vix = await fetch_quote(deps.http, _VIX_SYMBOL)
    if vix:
        lines.append(f"VIX 恐慌指數：{vix['price']:.1f}（{sign_of(vix['pct'])}{vix['pct']:.1f}%）")
        if vix["price"] >= 25 or vix["pct"] >= 10:
            notes.append("・VIX 跳升 → 國際避險情緒升溫，外資動作可能變大")

    if notes:
        lines.append("")
        lines.append("【📖 白話解讀】")
        lines += notes
    lines.append("＊僅陳列現況供判讀，非投資建議")
    return "\n".join(lines)


def _health_flex_lines(text: str) -> list[dict]:
    """體檢文字 → flex body 元件（解讀區用淡色小字）。"""
    components = []
    in_notes = False
    for line in text.split("\n")[1:]:  # 首行標題放 header
        if not line:
            continue
        if line.startswith("【📖"):
            in_notes = True
        size = "xxs" if in_notes else "xs"
        color = "#9E9E9E" if in_notes else "#333333"
        weight = {"weight": "bold"} if line.startswith("【") else {}
        components.append({"type": "text", "text": line, "size": size, "color": color, "wrap": True, **weight})
    return components


async def build_market_health_message(deps: Deps) -> dict:
    """大盤體檢卡片：hero 放加權指數走勢圖，body 放數據與白話解讀。"""
    text = await build_market_health(deps)
    title = text.split("\n")[0]
    bubble = {
        "type": "bubble",
        "size": "giga",
        "header": {
            "type": "box",
            "layout": "vertical",
            "backgroundColor": "#37474F",
            "paddingAll": "14px",
            "contents": [{"type": "text", "text": title, "size": "md", "weight": "bold", "color": "#FFFFFF"}],
        },
        "body": {"type": "box", "layout": "vertical", "spacing": "xs", "contents": _health_flex_lines(text)},
    }
    try:
        series_desc, _ = await _index_series_with_realtime(deps)
        if len(series_desc) >= 5:
            png = render_index_png(list(reversed(series_desc)))
            chart_id = deps.charts.put(png)
            bubble["hero"] = {
                "type": "image",
                "url": f"{deps.base_url}/charts/{chart_id}.png",
                "size": "full",
                "aspectRatio": "3:2",
                "aspectMode": "fit",
            }
    except Exception:
        logger.warning("大盤走勢圖繪製失敗，改純文字卡片", exc_info=True)
    return {"type": "flex", "altText": text[:400], "contents": bubble}
