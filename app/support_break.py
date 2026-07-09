"""支撐跌破法訊號（楊忠憲，2019）：門檻一律採「該股自身平均的倍數」，上市/上櫃分開設定。

參數化修正（相對倍數，不用固定百分比，避免參數過時）：
- 中長紅/黑：當日實體 ≥ 近20日平均實體的 body_mult 倍
- 量增：當日量 ≥ 前5日均量的 vol_mult 倍（上市 1.5、上櫃 2.0；量含當沖，僅供參考）
- 均線糾結：MA5/10/20 最大間距 ≤ 近20日平均日振幅
rows 一律由舊到新。
"""
from .indicators import sma
from .parser import format_number

_PARAMS = {
    "上市": {"vol_mult": 1.5},
    "上櫃": {"vol_mult": 2.0},
}
_DEFAULT_MARKET = "上市"
_BODY_MULT = 1.5          # 中長紅/黑：實體 ≥ 平均實體的 1.5 倍
_AVG_WINDOW = 20          # 平均實體/平均振幅的取樣天數
_BREAKOUT_WINDOW = 20     # 盤整區突破＝收盤創 N 日新高
_S4_LOOKBACK = 60         # S4 往回找突破紅K的範圍
_MIN_ROWS = 26            # 至少需要的資料天數


def _closes(rows: list[dict]) -> list[float]:
    return [r["close"] for r in rows if r.get("close") is not None]


def _avg_body_pct(rows: list[dict], n: int = _AVG_WINDOW) -> float | None:
    """近 n 日平均實體幅度%（|收-開|/前收）。"""
    samples = []
    for i in range(max(1, len(rows) - n), len(rows)):
        row, prev = rows[i], rows[i - 1]
        if row.get("open") is None or row.get("close") is None or prev.get("close") in (None, 0):
            continue
        samples.append(abs(row["close"] - row["open"]) / prev["close"] * 100)
    return sum(samples) / len(samples) if samples else None


def _avg_range_pct(rows: list[dict], n: int = _AVG_WINDOW) -> float | None:
    """近 n 日平均日振幅%（(高-低)/前收）。"""
    samples = []
    for i in range(max(1, len(rows) - n), len(rows)):
        row, prev = rows[i], rows[i - 1]
        if row.get("high") is None or row.get("low") is None or prev.get("close") in (None, 0):
            continue
        samples.append((row["high"] - row["low"]) / prev["close"] * 100)
    return sum(samples) / len(samples) if samples else None


def _body_pct(rows: list[dict], i: int) -> float | None:
    """第 i 日實體幅度%（帶正負：紅正黑負）。"""
    row, prev = rows[i], rows[i - 1]
    if row.get("open") is None or row.get("close") is None or prev.get("close") in (None, 0):
        return None
    return (row["close"] - row["open"]) / prev["close"] * 100


def _vol_ratio(rows: list[dict]) -> float | None:
    """今日量 / 前 5 日均量。"""
    if len(rows) < 6:
        return None
    volumes = [r.get("volume") for r in rows[-6:]]
    if any(v is None for v in volumes) or sum(volumes[:5]) == 0:
        return None
    return volumes[5] / (sum(volumes[:5]) / 5)


def _is_breakout(rows: list[dict], i: int, window: int = _BREAKOUT_WINDOW) -> bool:
    """第 i 日收盤是否創前 window 日收盤新高。"""
    if i < window:
        return False
    prior = [r["close"] for r in rows[i - window : i] if r.get("close") is not None]
    return bool(prior) and rows[i].get("close") is not None and rows[i]["close"] > max(prior)


def _ma_pair(closes: list[float], n: int) -> tuple[float | None, float | None]:
    """(今日MA, 昨日MA)。"""
    return sma(closes, n), sma(closes[:-1], n)


def evaluate_signals(rows: list[dict], market: str | None) -> dict | None:
    """回傳 {buy: [...], sell: [...], no_long: bool, params_note: str}；資料不足回 None。"""
    if len(rows) < _MIN_ROWS:
        return None
    closes = _closes(rows)
    if len(closes) < _MIN_ROWS:
        return None
    params = _PARAMS.get(market or "", _PARAMS[_DEFAULT_MARKET])
    close = closes[-1]
    ma5, ma5_prev = _ma_pair(closes, 5)
    ma10, ma10_prev = _ma_pair(closes, 10)
    ma20, ma20_prev = _ma_pair(closes, 20)
    avg_body = _avg_body_pct(rows)
    avg_range = _avg_range_pct(rows)
    body = _body_pct(rows, len(rows) - 1)
    vol_ratio = _vol_ratio(rows)

    long_red = body is not None and avg_body and body >= _BODY_MULT * avg_body
    long_black = body is not None and avg_body and body <= -_BODY_MULT * avg_body
    vol_surge = vol_ratio is not None and vol_ratio >= params["vol_mult"]
    breakout_today = _is_breakout(rows, len(rows) - 1)
    tangled = (
        avg_range is not None
        and all(v is not None for v in (ma5, ma10, ma20))
        and (max(ma5, ma10, ma20) - min(ma5, ma10, ma20)) / min(ma5, ma10, ma20) * 100 <= avg_range
    )

    buy = []
    # B1 積極：站上5日線且5日線上揚
    b1 = ma5 is not None and ma5_prev is not None and close > ma5 and ma5 > ma5_prev
    buy.append({
        "code": "B1", "on": b1,
        "text": f"站上5日線 {format_number(ma5)} 且上揚" if b1
        else f"未站上5日線 {format_number(ma5)} 或5日線未上揚",
    })
    # B2 保守：突破盤整區＋均線糾結＋中長紅＋量增
    b2 = breakout_today and tangled and bool(long_red) and vol_surge
    b2_facts = (
        f"創20日新高{'✓' if breakout_today else '✗'}、均線糾結{'✓' if tangled else '✗'}、"
        f"中長紅{'✓' if long_red else '✗'}、量 {vol_ratio:.1f} 倍{'✓' if vol_surge else '✗'}"
        if vol_ratio is not None
        else "量能資料不足"
    )
    buy.append({"code": "B2", "on": b2, "text": f"突破盤整區（{b2_facts}）" if b2 else b2_facts})
    # B3 加碼：今日突破且前20日內已有一次突破（二次突破）
    prior_breakout = any(_is_breakout(rows, i) for i in range(len(rows) - _BREAKOUT_WINDOW, len(rows) - 1))
    b3 = breakout_today and prior_breakout
    buy.append({
        "code": "B3", "on": b3,
        "text": "整理後二次突破" if b3 else "無二次突破",
    })

    sell = []
    # S1：跌破5日線且5日線下彎
    s1 = ma5 is not None and ma5_prev is not None and close < ma5 and ma5 < ma5_prev
    sell.append({
        "code": "S1", "on": s1,
        "text": f"跌破5日線 {format_number(ma5)} 且下彎" if s1 else f"5日線 {format_number(ma5)} 未跌破",
    })
    # S2：向下跳空缺口 或 中長黑
    prev_low = rows[-2].get("low")
    today_high = rows[-1].get("high")
    gap_down = prev_low is not None and today_high is not None and today_high < prev_low
    s2 = gap_down or bool(long_black)
    s2_parts = []
    if gap_down:
        s2_parts.append("向下跳空缺口")
    if long_black and body is not None and avg_body:
        s2_parts.append(f"中長黑（實體 {body:.1f}%＝平均 {avg_body:.1f}% 的 {abs(body) / avg_body:.1f} 倍）")
    sell.append({"code": "S2", "on": s2, "text": "、".join(s2_parts) if s2 else "無跳空缺口或中長黑"})
    # S3：跌破10日線下彎＋5/10日線死叉
    s3 = (
        all(v is not None for v in (ma10, ma10_prev, ma5))
        and close < ma10 and ma10 < ma10_prev and ma5 < ma10
    )
    sell.append({
        "code": "S3", "on": s3,
        "text": f"跌破10日線 {format_number(ma10)} 下彎＋5/10死叉" if s3 else f"10日線 {format_number(ma10)} 未跌破或無死叉",
    })
    # S4：跌破最近一次「突破盤整區的中長紅K」中間值
    s4 = False
    s4_text = "近期無突破紅K可對照"
    start = max(_BREAKOUT_WINDOW, len(rows) - _S4_LOOKBACK)
    for i in range(len(rows) - 1, start - 1, -1):
        body_i = _body_pct(rows, i)
        avg_body_i = _avg_body_pct(rows[: i + 1])
        if not (_is_breakout(rows, i) and body_i is not None and avg_body_i and body_i >= _BODY_MULT * avg_body_i):
            continue
        if rows[i].get("high") is None or rows[i].get("low") is None:
            continue
        mid = (rows[i]["high"] + rows[i]["low"]) / 2
        s4 = close < mid
        date = str(rows[i].get("trade_date", ""))[5:]
        s4_text = (
            f"跌破突破紅K（{date}）中間值 {format_number(mid)}" if s4
            else f"守住突破紅K（{date}）中間值 {format_number(mid)}"
        )
        break
    sell.append({"code": "S4", "on": s4, "text": s4_text})
    # S5：近5日收盤都在10日線下（反彈不過）＋跌破20日線且下彎
    s5 = (
        all(v is not None for v in (ma10, ma20, ma20_prev))
        and max(closes[-5:]) < ma10
        and close < ma20 and ma20 < ma20_prev
    )
    sell.append({
        "code": "S5", "on": s5,
        "text": f"反彈不過10日線＋跌破20日線 {format_number(ma20)} 下彎" if s5 else "未同時符合（反彈不過10日線＋破下彎20日線）",
    })

    no_long = ma20 is not None and ma20_prev is not None and ma20 < ma20_prev
    vol_note = f"量增門檻：5日均量 {params['vol_mult']} 倍（{market or _DEFAULT_MARKET}）"
    return {
        "buy": buy,
        "sell": sell,
        "no_long": no_long,
        "params_note": f"{vol_note}；中長紅黑＝實體≥20日平均實體 {_BODY_MULT} 倍",
    }


def format_signal_report(stock: dict, close: float, trade_date: str, result: dict) -> str:
    """完整訊號報告（「訊號XXXX」指令用）。"""
    market = f"・{stock['market']}" if stock.get("market") else ""
    lines = [
        f"🛡 支撐跌破法訊號｜{stock['stock_no']} {stock['name']}{market}",
        f"收盤 {format_number(close)}（{trade_date[5:] if len(trade_date) >= 10 else trade_date}）",
        "",
        "📈 買進訊號",
    ]
    for item in result["buy"]:
        lines.append(f"{item['code']} {'✅' if item['on'] else '－'} {item['text']}")
    lines += ["", "📉 賣出訊號"]
    for item in result["sell"]:
        lines.append(f"{item['code']} {'🚨' if item['on'] else '－'} {item['text']}")
    if result["no_long"]:
        lines += ["", "⛔ 20日線下彎：此股不做多（持有者出現賣訊即減碼）"]
    lines += ["", f"（{result['params_note']}）"]
    return "\n".join(lines)


def signal_summary_line(result: dict | None) -> str | None:
    """圖卡摘要行：賣出訊號 n/5。"""
    if not result:
        return None
    fired = [item["code"] for item in result["sell"] if item["on"]]
    if fired:
        return f"🚨 支撐跌破：賣出訊號 {len(fired)}/5（{'、'.join(fired)}）"
    return "🟢 支撐跌破：無賣出訊號（0/5）"
