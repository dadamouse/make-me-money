import json

from app.health import build_health_report
from app.indicators import ema_series, macd_series, obv_series

from test_bot import BotRuntime, _seed_history


def _rows(closes, volumes=None):
    volumes = volumes or [1000] * len(closes)
    return [
        {"trade_date": f"2026-01-{i + 1:02d}", "close": c, "open": c, "high": c + 1, "low": c - 1, "volume": v}
        for i, (c, v) in enumerate(zip(closes, volumes))
    ]


def test_ema_series_hand_computed():
    # EMA(3) of [1,2,3,4,5]：SMA 起算 = 2，之後 (x-ema)*0.5+ema
    result = ema_series([1, 2, 3, 4, 5], 3)
    assert result == [None, None, 2.0, 3.0, 4.0]


def test_macd_series_uptrend_is_bullish():
    closes = [100.0 + i for i in range(40)]
    dif, signal, hist = macd_series(closes)
    assert dif[-1] is not None and signal[-1] is not None
    # 等速上漲末段 DIF 收斂到常數並貼近訊號線，兩者皆為正
    assert dif[-1] > 0 and signal[-1] > 0


def test_obv_series_hand_computed():
    rows = _rows([10, 11, 10.5, 10.5, 12], [100, 200, 300, 400, 500])
    # +200（漲）、-300（跌）、0（平）、+500（漲）→ [0, 200, -100, -100, 400]
    assert obv_series(rows) == [0, 200, -100, -100, 400]


def test_health_report_uptrend():
    closes = [100.0 + i * 0.3 for i in range(60)]
    report = build_health_report(_rows(closes))
    assert report is not None
    assert report.splitlines()[0].startswith("📋 技術體檢")
    assert "✅ 20日均線向上" in report
    assert "✅ MACD 多方" in report
    assert "✅ OBV 走升" in report
    assert "乖離率" in report
    assert "✅ 價量同步（無背離）" in report


def test_health_report_downtrend():
    closes = [200.0 - i * 0.5 for i in range(60)]
    report = build_health_report(_rows(closes))
    assert "❌ 20日均線向下" in report
    assert "❌ MACD 空方" in report
    assert "❌ OBV 走降" in report


def test_health_report_divergence_price_up_volume_out():
    # 漲日小量、跌日大量：5 日價漲 +7 但 OBV -170 → 背離
    closes = [100.0] * 30 + [100, 105, 101, 106, 102, 107]
    volumes = [1000] * 30 + [1000, 10, 100, 10, 100, 10]
    report = build_health_report(_rows(closes, volumes))
    assert "⚠️ 價量背離：價漲但量能未跟上" in report


def test_health_report_insufficient_data():
    assert build_health_report(_rows([100.0, 101.0, 102.0])) is None


def test_chart_command_includes_health_report():
    with BotRuntime() as rt:
        _seed_history(rt, "2330")
        rt.send("登入dada")
        rt.send("圖2330")
        content = json.dumps(rt.last_message()["contents"], ensure_ascii=False)
        assert "技術體檢" in content
        assert "20日均線向上" in content


def test_ma20_deduction_close_above_and_falling_deduction():
    """收盤高於扣抵值 → 月線易續揚；未來 5 筆扣抵一路走低 → 助漲。"""
    # 舊到新：前段 120→100 遞減（未來扣抵走低），後段回升到 150
    closes = [120.0 - i for i in range(20)] + [130.0 + i for i in range(10)]
    report = build_health_report(_rows(closes))
    assert "✅ 月線扣抵 110（收盤在上，月線易續揚；未來一週扣抵走低（助漲））" in report


def test_ma20_deduction_close_below_and_rising_deduction():
    """收盤低於扣抵值 → 月線轉下彎；未來 5 筆扣抵走高 → 助跌。"""
    closes = [100.0 + i for i in range(25)] + [80.0] * 5
    report = build_health_report(_rows(closes))
    assert "❌ 月線扣抵" in report
    assert "未來一週扣抵走高（助跌）" in report


def test_ma20_deduction_needs_20_closes():
    report = build_health_report(_rows([100.0] * 19))
    assert report is None or "扣抵" not in report
