from app.support_break import evaluate_signals, signal_summary_line

from test_bot import BotRuntime, _seed_history


def _rows(specs):
    """specs: (close, open, high, low, volume)。"""
    return [
        {"trade_date": f"2026-03-{i + 1:02d}" if i < 30 else f"2026-04-{i - 29:02d}",
         "close": c, "open": o, "high": h, "low": lo, "volume": v}
        for i, (c, o, h, lo, v) in enumerate(specs)
    ]


def _flat_days(n, price=100.0, volume=1000):
    return [(price, price, price + 1, price - 1, volume)] * n


def _by_code(items):
    return {item["code"]: item for item in items}


def test_uptrend_fires_b1_no_sell():
    specs = [(100.0 + i, 99.5 + i, 101.0 + i, 99.0 + i, 1000) for i in range(30)]
    result = evaluate_signals(_rows(specs), "上市")
    buy, sell = _by_code(result["buy"]), _by_code(result["sell"])
    assert buy["B1"]["on"] is True
    assert all(not item["on"] for item in sell.values())
    assert result["no_long"] is False
    assert signal_summary_line(result) == "🟢 支撐跌破：無賣出訊號（0/5）"


def test_downtrend_fires_s1_s3_s5_and_no_long():
    specs = [(200.0 - i, 200.5 - i, 201.0 - i, 199.0 - i, 1000) for i in range(30)]
    result = evaluate_signals(_rows(specs), "上市")
    sell = _by_code(result["sell"])
    assert sell["S1"]["on"] is True
    assert sell["S3"]["on"] is True
    assert sell["S5"]["on"] is True
    assert result["no_long"] is True
    assert "S1、S3、S5" in signal_summary_line(result)


def test_breakout_b2_market_split():
    """盤整 30 日後帶量中長紅突破：量 1.6 倍 → 上市過（1.5）、上櫃不過（2.0）。"""
    specs = _flat_days(30) + [(106.0, 100.0, 107.0, 99.0, 1600)]
    listed = _by_code(evaluate_signals(_rows(specs), "上市")["buy"])
    otc = _by_code(evaluate_signals(_rows(specs), "上櫃")["buy"])
    assert listed["B2"]["on"] is True
    assert otc["B2"]["on"] is False
    assert "量 1.6 倍" in listed["B2"]["text"]


def test_s4_breaks_breakout_candle_midpoint():
    """突破紅K（高107低99，中間值103）後跌到 102 → S4 觸發。"""
    specs = _flat_days(30) + [(106.0, 100.0, 107.0, 99.0, 2000)] + [
        (104.0, 105.0, 105.5, 103.5, 1000),
        (102.0, 103.5, 104.0, 101.5, 1000),
    ]
    sell = _by_code(evaluate_signals(_rows(specs), "上市")["sell"])
    assert sell["S4"]["on"] is True
    assert "103" in sell["S4"]["text"]


def test_s2_gap_down():
    """昨日最低 99、今日最高 98 → 向下跳空缺口。"""
    specs = _flat_days(29) + [(100.0, 100.0, 101.0, 99.0, 1000), (97.0, 98.0, 98.0, 96.5, 1000)]
    sell = _by_code(evaluate_signals(_rows(specs), "上市")["sell"])
    assert sell["S2"]["on"] is True
    assert "向下跳空缺口" in sell["S2"]["text"]


def test_insufficient_data_returns_none():
    specs = _flat_days(10)
    assert evaluate_signals(_rows(specs), "上市") is None
    assert signal_summary_line(None) is None


def test_signal_command_end_to_end():
    with BotRuntime() as rt:
        _seed_history(rt, "2330")
        rt.send("登入dada")
        rt.send("訊號2330")
        reply = rt.last_reply()
        assert "🛡 支撐跌破法訊號｜2330 台積電" in reply
        assert "📈 買進訊號" in reply
        assert "📉 賣出訊號" in reply
        assert "量增門檻：5日均量 1.5 倍（上市）" in reply
