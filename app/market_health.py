"""大盤體檢：只陳列事實供判讀，不做預測（A 方案）。"""
import logging
from datetime import datetime, timedelta, timezone

from .chart import render_index_png
from .deps import Deps
from .indicators import rsi
from .parser import format_number, sign_of
from .premarket import USDTWD_SYMBOL, fetch_quote

logger = logging.getLogger(__name__)

_TAIPEI_TZ = timezone(timedelta(hours=8))
_VIX_SYMBOL = "^VIX"


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


async def build_market_health(deps: Deps) -> str:
    lines = []
    notes = []  # 白話解讀（描述現況，不做預測）
    series_desc = await deps.db.rpc("market_series", {"n": 60})
    closes_asc = [float(row["taiex"]) for row in reversed(series_desc)]
    amounts_asc = [float(row["amount"]) for row in reversed(series_desc) if row.get("amount") is not None]

    if len(closes_asc) >= 2:
        latest, prev = closes_asc[-1], closes_asc[-2]
        pct = (latest - prev) / prev * 100
        date_label = str(series_desc[0]["trade_date"])[5:].replace("-", "/")
        lines.append(f"📋 大盤體檢（{date_label}）")
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
            lines.append(f"量能：5 日均量的 {ratio:.2f} 倍")
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

    fx = await fetch_quote(deps.http, USDTWD_SYMBOL)
    if fx:
        direction = "台幣貶" if fx["pct"] >= 0 else "台幣升"
        lines.append(f"匯率：美元/台幣 {fx['price']:.2f}（{sign_of(fx['pct'])}{fx['pct']:.2f}%，{direction}）")
        if fx["pct"] >= 0.3:
            notes.append("・台幣明顯走貶 → 外資資金偏匯出，對台股是逆風")
        elif fx["pct"] <= -0.3:
            notes.append("・台幣明顯走升 → 外資資金偏流入，對台股是助力")

    flows = await deps.db.rpc("market_flow_series", {"n": 10})
    insti_values = [float(row["insti_net"]) for row in flows if row.get("insti_net") is not None]
    if insti_values:
        days, direction = _streak(insti_values)
        lines.append(f"法人：今日 {sign_of(insti_values[0])}{format_number(round(insti_values[0] / 1000))} 張（連 {days} 日{direction}）")
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
        series_desc = await deps.db.rpc("market_series", {"n": 60})
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
