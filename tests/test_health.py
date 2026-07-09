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
