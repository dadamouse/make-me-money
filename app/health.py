"""個股技術體檢：事實陳列＋白話說明，不做漲跌預測。rows 一律由舊到新。"""
from .indicators import kd_series, macd_series, obv_series, rsi_series, sma
from .parser import format_number

_MA_SLOPE_LOOKBACK = 3     # MA20 斜率：與 3 日前比較
_TREND_LOOKBACK = 5        # OBV／價量背離：近 5 日方向
_BIAS_WARN_PCT = 7.0       # 20 日乖離率 ±7% 內視為未過熱
_KD_HOT, _KD_COLD = 80.0, 20.0
_RSI_HOT, _RSI_COLD = 70.0, 30.0
_DEDUCTION_TREND_PCT = 1.0  # 未來 5 筆扣抵值變動 ±1% 內視為持平


def _check_ma20(closes: list[float]) -> dict | None:
    """MA20 斜率：今日 vs 3 日前。"""
    if len(closes) < 20 + _MA_SLOPE_LOOKBACK:
        return None
    now = sma(closes, 20)
    before = sma(closes[:-_MA_SLOPE_LOOKBACK], 20)
    if now is None or before is None:
        return None
    if now > before:
        return {"ok": True, "text": "✅ 20日均線向上（趨勢偏多）"}
    if now < before:
        return {"ok": False, "text": "❌ 20日均線向下（趨勢偏空）"}
    return {"ok": True, "text": "✅ 20日均線走平"}


def _check_ma20_deduction(closes: list[float]) -> dict | None:
    """月線扣抵：明日將被扣掉的舊收盤 vs 今日收盤 → 預判 MA20 方向；再看未來 5 筆扣抵走向。"""
    if len(closes) < 20:
        return None
    deduction = closes[-20]  # 明日換新收盤時被扣掉的那筆
    future = closes[-20:-15]  # 未來 5 個交易日依序被扣掉的舊價
    trend_pct = (future[-1] - future[0]) / future[0] * 100
    if trend_pct <= -_DEDUCTION_TREND_PCT:
        trend = "；未來一週扣抵走低（助漲）"
    elif trend_pct >= _DEDUCTION_TREND_PCT:
        trend = "；未來一週扣抵走高（助跌）"
    else:
        trend = ""
    if closes[-1] >= deduction:
        return {"ok": True, "text": f"✅ 月線扣抵 {format_number(deduction)}（收盤在上，月線易續揚{trend}）"}
    return {"ok": False, "text": f"❌ 月線扣抵 {format_number(deduction)}（收盤在下，月線轉下彎{trend}）"}


def _check_macd(closes: list[float]) -> dict | None:
    dif, signal, hist = macd_series(closes)
    if dif[-1] is None or signal[-1] is None:
        return None
    above = dif[-1] > signal[-1]
    momentum = ""
    if len(hist) >= 2 and hist[-1] is not None and hist[-2] is not None:
        momentum = "、動能增強" if abs(hist[-1]) > abs(hist[-2]) and above else ""
        if not above:
            momentum = "、跌勢趨緩" if abs(hist[-1]) < abs(hist[-2]) else ""
    if above:
        return {"ok": True, "text": f"✅ MACD 多方（DIF 在訊號線上{momentum}）"}
    return {"ok": False, "text": f"❌ MACD 空方（DIF 在訊號線下{momentum}）"}


def _check_kd(rows: list[dict]) -> dict | None:
    k_out, d_out, _ = kd_series(rows)
    k, d = k_out[-1], d_out[-1]
    if k is None or d is None:
        return None
    if k > _KD_HOT:
        return {"ok": False, "text": f"⚠️ KD 高檔（K {k:.0f} > {_KD_HOT:.0f}，短線過熱注意）"}
    if k < _KD_COLD:
        return {"ok": False, "text": f"⚠️ KD 低檔（K {k:.0f} < {_KD_COLD:.0f}，超賣區）"}
    direction = "K 在 D 上（偏多）" if k > d else "K 在 D 下（偏弱）"
    return {"ok": k > d, "text": f"{'✅' if k > d else '❌'} KD 正常區（{direction}）"}


def _check_rsi(closes: list[float]) -> dict | None:
    series = rsi_series(closes)
    value = series[-1]
    if value is None:
        return None
    if value > _RSI_HOT:
        return {"ok": False, "text": f"⚠️ RSI {value:.0f} 過熱（>{_RSI_HOT:.0f}）"}
    if value < _RSI_COLD:
        return {"ok": False, "text": f"⚠️ RSI {value:.0f} 超賣（<{_RSI_COLD:.0f}）"}
    return {"ok": True, "text": f"✅ RSI {value:.0f} 正常區間"}


def _has_volumes(rows: list[dict]) -> bool:
    recent = rows[-(_TREND_LOOKBACK + 1):]
    return len(recent) == _TREND_LOOKBACK + 1 and all(r.get("volume") is not None for r in recent)


def _check_obv(rows: list[dict]) -> dict | None:
    if not _has_volumes(rows):
        return None
    obv = obv_series(rows)
    now, before = obv[-1], obv[-1 - _TREND_LOOKBACK]
    if now is None or before is None:
        return None
    if now > before:
        return {"ok": True, "text": "✅ OBV 走升（買盤量能進場）"}
    if now < before:
        return {"ok": False, "text": "❌ OBV 走降（量能流出）"}
    return {"ok": True, "text": "✅ OBV 持平"}


def _check_bias(closes: list[float]) -> dict | None:
    ma20 = sma(closes, 20)
    if ma20 is None or not ma20:
        return None
    bias = (closes[-1] - ma20) / ma20 * 100
    if abs(bias) <= _BIAS_WARN_PCT:
        return {"ok": True, "text": f"✅ 乖離率 {bias:+.1f}%（未過度偏離月線）"}
    side = "正乖離過大（漲多離月線遠，回檔風險）" if bias > 0 else "負乖離過大（跌深離月線遠）"
    return {"ok": False, "text": f"⚠️ 乖離率 {bias:+.1f}%：{side}"}


def _check_divergence(rows: list[dict]) -> dict | None:
    """價量背離：近 5 日價方向 vs OBV 方向。"""
    closes = [r["close"] for r in rows if r.get("close") is not None]
    if len(closes) < _TREND_LOOKBACK + 1 or not _has_volumes(rows):
        return None
    price_delta = closes[-1] - closes[-1 - _TREND_LOOKBACK]
    obv = obv_series(rows)
    obv_delta = (obv[-1] or 0) - (obv[-1 - _TREND_LOOKBACK] or 0)
    if price_delta > 0 and obv_delta < 0:
        return {"ok": False, "text": "⚠️ 價量背離：價漲但量能未跟上（追高留意）"}
    if price_delta < 0 and obv_delta > 0:
        return {"ok": False, "text": "⚠️ 價量背離：價跌但買盤量能增（賣壓未必持續）"}
    return {"ok": True, "text": "✅ 價量同步（無背離）"}


def build_health_report(rows: list[dict]) -> str | None:
    """回傳多行體檢文字；資料不足算不出任何一項時回 None。"""
    closes = [r["close"] for r in rows if r.get("close") is not None]
    checks = [
        _check_ma20(closes),
        _check_ma20_deduction(closes),
        _check_macd(closes),
        _check_kd(rows),
        _check_rsi(closes),
        _check_obv(rows),
        _check_bias(closes),
        _check_divergence(rows),
    ]
    done = [c for c in checks if c]
    if not done:
        return None
    passed = sum(1 for c in done if c["ok"])
    lines = [f"📋 技術體檢 {passed}/{len(done)} 過關"] + [c["text"] for c in done]
    return "\n".join(lines)
