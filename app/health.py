"""個股技術體檢：事實陳列＋白話說明，不做漲跌預測。rows 一律由舊到新。

每項檢查回傳 {"ok", "text", "why"}：text 是體檢清單的一行摘要，
why 是給「買進檢查」用的詳細白話（這個數字代表什麼、該注意什麼）。
"""
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
        return {
            "ok": True,
            "text": "✅ 20日均線向上（趨勢偏多）",
            "why": "月線可視為近一個月進場者的平均成本：現價在上且均線上彎，代表這一個月買的人平均有賺，回檔時較容易有人願意加碼承接。",
        }
    if now < before:
        return {
            "ok": False,
            "text": "❌ 20日均線向下（趨勢偏空）",
            "why": "月線下彎代表近一個月進場的人平均被套牢，反彈容易遇到解套賣壓；逆著月線方向做多，勝率統計上明顯較差。",
        }
    return {
        "ok": True,
        "text": "✅ 20日均線走平",
        "why": "月線走平代表多空僵持、方向未定；這種盤整段進出成本高，等價格帶量脫離區間再動作較有效率。",
    }


def _check_ma20_deduction(closes: list[float]) -> dict | None:
    """月線扣抵：明日將被扣掉的舊收盤 vs 今日收盤 → 預判 MA20 方向；再看未來 5 筆扣抵走向。"""
    if len(closes) < 20:
        return None
    deduction = closes[-20]  # 明日換新收盤時被扣掉的那筆
    future = closes[-20:-15]  # 未來 5 個交易日依序被扣掉的舊價
    trend_pct = (future[-1] - future[0]) / future[0] * 100
    if trend_pct <= -_DEDUCTION_TREND_PCT:
        trend = "；未來一週扣抵走低（助漲）"
        trend_why = "接下來一週要被扣掉的都是更低的舊價，月線易翻揚，是均線的「助漲段」。"
    elif trend_pct >= _DEDUCTION_TREND_PCT:
        trend = "；未來一週扣抵走高（助跌）"
        trend_why = "接下來一週要扣掉的舊價越來越高，月線會被墊高的舊價拖累，是均線的「助跌段」。"
    else:
        trend = ""
        trend_why = ""
    if closes[-1] >= deduction:
        return {
            "ok": True,
            "text": f"✅ 月線扣抵 {format_number(deduction)}（收盤在上，月線易續揚{trend}）",
            "why": f"扣抵值是明天要從月線平均裡「扣掉」的 20 天前舊價：收盤高於它，月線明天就會續升，均線支撐力道持續。{trend_why}",
        }
    return {
        "ok": False,
        "text": f"❌ 月線扣抵 {format_number(deduction)}（收盤在下，月線轉下彎{trend}）",
        "why": f"收盤低於扣抵值 {format_number(deduction)}，月線明天起轉下彎——原本的支撐會逐漸變成反壓。{trend_why}",
    }


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
        return {
            "ok": True,
            "text": f"✅ MACD 多方（DIF 在訊號線上{momentum}）",
            "why": "MACD 反映中期動能：DIF 站上訊號線代表漲勢的「速度」還在加快側，紅柱放大時多方力道增強；這是波段偏多的訊號，不是進場點本身。",
        }
    return {
        "ok": False,
        "text": f"❌ MACD 空方（DIF 在訊號線下{momentum}）",
        "why": "DIF 在訊號線下代表中期動能偏空，反彈多屬跌深反彈性質；要等 DIF 重新上穿訊號線，波段結構才算翻多。"
        + ("目前綠柱在縮短，跌勢有趨緩跡象。" if momentum else ""),
    }


def _check_kd(rows: list[dict]) -> dict | None:
    k_out, d_out, _ = kd_series(rows)
    k, d = k_out[-1], d_out[-1]
    if k is None or d is None:
        return None
    if k > _KD_HOT:
        return {
            "ok": False,
            "text": f"⚠️ KD 高檔（K {k:.0f} > {_KD_HOT:.0f}，短線過熱注意）",
            "why": f"K 值 {k:.0f} 已進入超買區（>80）：不代表一定會跌，強勢股可以鈍化在高檔，但「此刻進場」的人統計上常買在短線高點，追價勝率不佳。",
        }
    if k < _KD_COLD:
        return {
            "ok": False,
            "text": f"⚠️ KD 低檔（K {k:.0f} < {_KD_COLD:.0f}，超賣區）",
            "why": f"K 值 {k:.0f} 在超賣區（<20）：跌深但「超賣可以更超賣」，單獨這一項不是買點，等 K 回頭上穿 D 再考慮比較穩。",
        }
    direction = "K 在 D 上（偏多）" if k > d else "K 在 D 下（偏弱）"
    why = (
        f"K（{k:.0f}）在 D（{d:.0f}）之上且未過熱：短線動能偏多、也還沒買貴，是 KD 指標裡相對舒服的進場區。"
        if k > d
        else f"K（{k:.0f}）在 D（{d:.0f}）之下：短線動能偏弱，買進等於接還在下墜的刀，等黃金交叉再說。"
    )
    return {"ok": k > d, "text": f"{'✅' if k > d else '❌'} KD 正常區（{direction}）", "why": why}


def _check_rsi(closes: list[float]) -> dict | None:
    series = rsi_series(closes)
    value = series[-1]
    if value is None:
        return None
    if value > _RSI_HOT:
        return {
            "ok": False,
            "text": f"⚠️ RSI {value:.0f} 過熱（>{_RSI_HOT:.0f}）",
            "why": f"RSI {value:.0f} 代表近 14 天漲勢佔比極高：市場情緒偏亢奮，隨時可能技術性回檔，此時進場要有「買完先套一段」的心理準備。",
        }
    if value < _RSI_COLD:
        return {
            "ok": False,
            "text": f"⚠️ RSI {value:.0f} 超賣（<{_RSI_COLD:.0f}）",
            "why": f"RSI {value:.0f} 屬超賣：跌勢佔比極高、情緒悲觀。反彈隨時可能出現，但下跌趨勢中 RSI 可以長期趴在低檔，別把超賣當買點。",
        }
    return {
        "ok": True,
        "text": f"✅ RSI {value:.0f} 正常區間",
        "why": f"RSI {value:.0f} 在 30–70 的正常區：情緒不極端，指標本身不構成進出場理由，看趨勢與籌碼面決定。",
    }


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
        return {
            "ok": True,
            "text": "✅ OBV 走升（買盤量能進場）",
            "why": "OBV 把每天的成交量依漲跌累加：近 5 日走升代表「用真金白銀投票」的買方在進場，上漲有量支撐、不是虛漲。",
        }
    if now < before:
        return {
            "ok": False,
            "text": "❌ OBV 走降（量能流出）",
            "why": "OBV 近 5 日走降代表量能在流出：若價格還撐在高檔，屬「價撐量縮」，後繼無力的風險高。",
        }
    return {"ok": True, "text": "✅ OBV 持平", "why": "近 5 日量能進出大致平衡，量的面向沒有明顯訊號。"}


def _check_bias(closes: list[float]) -> dict | None:
    ma20 = sma(closes, 20)
    if ma20 is None or not ma20:
        return None
    bias = (closes[-1] - ma20) / ma20 * 100
    if abs(bias) <= _BIAS_WARN_PCT:
        return {
            "ok": True,
            "text": f"✅ 乖離率 {bias:+.1f}%（未過度偏離月線）",
            "why": f"現價距月線 {bias:+.1f}%，在 ±7% 的正常範圍：就算看好進場，也不算追高買貴。",
        }
    side = "正乖離過大（漲多離月線遠，回檔風險）" if bias > 0 else "負乖離過大（跌深離月線遠）"
    why = (
        f"現價高出月線 {bias:.1f}%：漲太快離成本區太遠，統計上容易「回測月線」修正，此刻追價短線易套；急漲後常見的劇本是回踩月線再上。"
        if bias > 0
        else f"現價低於月線 {abs(bias):.1f}%：跌深乖離大，反彈隨時可能出現，但這是搶反彈的邏輯而非波段買點，部位要小、停損要快。"
    )
    return {"ok": False, "text": f"⚠️ 乖離率 {bias:+.1f}%：{side}", "why": why}


def _check_divergence(rows: list[dict]) -> dict | None:
    """價量背離：近 5 日價方向 vs OBV 方向。"""
    closes = [r["close"] for r in rows if r.get("close") is not None]
    if len(closes) < _TREND_LOOKBACK + 1 or not _has_volumes(rows):
        return None
    price_delta = closes[-1] - closes[-1 - _TREND_LOOKBACK]
    obv = obv_series(rows)
    obv_delta = (obv[-1] or 0) - (obv[-1 - _TREND_LOOKBACK] or 0)
    if price_delta > 0 and obv_delta < 0:
        return {
            "ok": False,
            "text": "⚠️ 價量背離：價漲但量能未跟上（追高留意）",
            "why": "近 5 日價格上漲但 OBV 量能反而流出：漲勢缺乏真實買盤支撐，常見於出貨段的拉抬，追高風險偏高。",
        }
    if price_delta < 0 and obv_delta > 0:
        return {
            "ok": False,
            "text": "⚠️ 價量背離：價跌但買盤量能增（賣壓未必持續）",
            "why": "近 5 日價格下跌但 OBV 走升：有人趁跌承接，賣壓未必能持續——偏中性訊號，觀察是否止穩。",
        }
    return {"ok": True, "text": "✅ 價量同步（無背離）", "why": "價格方向與量能方向一致，走勢的「可信度」正常，沒有背離警訊。"}


def detailed_checks(rows: list[dict]) -> list[dict]:
    """回傳所有算得出來的檢查（含 ok/text/why），供技術體檢與買進檢查共用。"""
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
    return [c for c in checks if c]


def build_health_report(rows: list[dict]) -> str | None:
    """回傳多行體檢文字；資料不足算不出任何一項時回 None。"""
    done = detailed_checks(rows)
    if not done:
        return None
    passed = sum(1 for c in done if c["ok"])
    lines = [f"📋 技術體檢 {passed}/{len(done)} 過關"] + [c["text"] for c in done]
    return "\n".join(lines)
