"""技術指標純函式。rows 一律由舊到新排序。"""

KD_PERIOD = 9


def sma(closes: list[float], n: int) -> float | None:
    if len(closes) < n:
        return None
    return sum(closes[-n:]) / n


def rsi(closes: list[float], period: int = 14) -> float | None:
    """Wilder 平滑 RSI。"""
    if len(closes) < period + 1:
        return None
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    avg_gain = sum(d for d in deltas[:period] if d > 0) / period
    avg_loss = sum(-d for d in deltas[:period] if d < 0) / period
    for delta in deltas[period:]:
        avg_gain = (avg_gain * (period - 1) + max(delta, 0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-delta, 0)) / period
    if avg_loss == 0:
        return 100.0
    return 100 - 100 / (1 + avg_gain / avg_loss)


def stochastic_kd(rows: list[dict], period: int = KD_PERIOD) -> tuple[float | None, float | None]:
    """台股常用 KD(9)：K = 2/3·K′＋1/3·RSV，D = 2/3·D′＋1/3·K，起始值 50。
    只使用連續具備 high/low/close 的最後一段資料。
    """
    usable: list[dict] = []
    for row in reversed(rows):
        if row.get("high") is None or row.get("low") is None or row.get("close") is None:
            break
        usable.append(row)
    usable.reverse()
    if len(usable) < period:
        return None, None
    k = d = 50.0
    for i in range(period - 1, len(usable)):
        window = usable[i - period + 1 : i + 1]
        highest = max(r["high"] for r in window)
        lowest = min(r["low"] for r in window)
        rsv = 50.0 if highest == lowest else (usable[i]["close"] - lowest) / (highest - lowest) * 100
        k = k * 2 / 3 + rsv / 3
        d = d * 2 / 3 + k / 3
    return k, d


def compute_indicators(rows: list[dict]) -> dict:
    closes = [r["close"] for r in rows if r.get("close") is not None]
    k, d = stochastic_kd(rows)
    return {
        "ma5": sma(closes, 5),
        "ma20": sma(closes, 20),
        "ma60": sma(closes, 60),
        "rsi14": rsi(closes, 14),
        "k": k,
        "d": d,
    }
