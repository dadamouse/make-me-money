from app.history import parse_tpex_month_rows, parse_twse_month_rows
from app.indicators import compute_indicators, rsi, sma, stochastic_kd


def _rows(closes, spread=1.0):
    return [
        {"trade_date": f"2026-01-{i + 1:02d}", "close": c, "high": c + spread, "low": c - spread}
        for i, c in enumerate(closes)
    ]


def test_sma():
    assert sma([1, 2, 3, 4, 5], 5) == 3
    assert sma([1, 2, 3], 5) is None
    assert sma([10, 20, 30, 40], 2) == 35


def test_rsi_all_gains_is_100():
    closes = list(range(1, 20))
    assert rsi(closes, 14) == 100.0


def test_rsi_known_value():
    # 一半漲一半跌、幅度相同 → RSI 約 50
    closes = [100]
    for i in range(30):
        closes.append(closes[-1] + (1 if i % 2 == 0 else -1))
    value = rsi(closes, 14)
    assert 40 <= value <= 60


def test_rsi_insufficient_data():
    assert rsi([1, 2, 3], 14) is None


def test_kd_uptrend_high():
    rows = _rows([float(100 + i) for i in range(30)])
    k, d = stochastic_kd(rows)
    assert k > 70
    assert d > 60


def test_kd_insufficient_or_missing_high_low():
    assert stochastic_kd(_rows([1, 2, 3])) == (None, None)
    rows = _rows([float(i) for i in range(30)])
    rows[-1]["high"] = None  # 最後一段缺高低價 → 無法計算
    assert stochastic_kd(rows) == (None, None)


def test_compute_indicators_full_and_partial():
    rows = _rows([float(100 + i) for i in range(70)])
    ind = compute_indicators(rows)
    assert ind["ma5"] == sum(range(165, 170)) / 5
    assert ind["ma60"] is not None
    assert ind["rsi14"] == 100.0
    assert ind["k"] is not None

    short = compute_indicators(_rows([1.0, 2.0]))
    assert short["ma5"] is None
    assert short["rsi14"] is None
    assert short["k"] is None


def test_parse_twse_month_rows():
    api = {
        "stat": "OK",
        "data": [["115/07/01", "31,058,614", "x", "2,400", "2,470", "2,390", "2,465.00", "+65", "100"]],
    }
    rows = parse_twse_month_rows(api, "2330")
    assert rows == [
        {
            "stock_no": "2330",
            "trade_date": "2026-07-01",
            "close": 2465.0,
            "high": 2470.0,
            "low": 2390.0,
            "volume": 31058614.0,
        }
    ]
    assert parse_twse_month_rows({"stat": "沒有資料"}, "2330") == []


def test_parse_tpex_month_rows_converts_lots_to_shares():
    api = {"tables": [{"data": [["115/07/01", "278", "4,840,252", "17,105", "17,780", "16,750", "17,500", "+395", "50"]]}]}
    rows = parse_tpex_month_rows(api, "5274")
    assert rows[0]["volume"] == 278000.0
    assert rows[0]["close"] == 17500.0
    assert parse_tpex_month_rows({}, "5274") == []
