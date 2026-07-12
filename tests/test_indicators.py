from app.history import parse_tpex_month_rows, parse_twse_month_rows
from app.indicators import bollinger_series, compute_indicators, kd_series, rsi, rsi_series, sma, stochastic_kd


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
    assert ind["j"] == 3 * ind["k"] - 2 * ind["d"]

    short = compute_indicators(_rows([1.0, 2.0]))
    assert short["ma5"] is None
    assert short["rsi14"] is None
    assert short["k"] is None
    assert short["j"] is None


def test_rsi_series_matches_scalar_rsi():
    closes = [100.0 + (i % 7) - 3 for i in range(40)]
    series = rsi_series(closes)
    assert series[:14] == [None] * 14
    assert abs(series[-1] - rsi(closes)) < 1e-9
    assert all(v is not None for v in series[14:])


def test_kd_series_matches_scalar_kd():
    rows = [
        {"trade_date": f"2026-01-{i + 1:02d}", "close": 100.0 + i, "high": 102.0 + i, "low": 98.0 + i}
        for i in range(30)
    ]
    k_values, d_values, j_values = kd_series(rows)
    assert k_values[:8] == [None] * 8
    k, d = stochastic_kd(rows)
    assert abs(k_values[-1] - k) < 1e-9
    assert abs(d_values[-1] - d) < 1e-9
    assert abs(j_values[-1] - (3 * k - 2 * d)) < 1e-9


def test_kd_series_skips_days_with_missing_high_low():
    rows = [
        {"trade_date": f"2026-01-{i + 1:02d}", "close": 100.0 + i, "high": 102.0 + i, "low": 98.0 + i}
        for i in range(20)
    ]
    rows[12]["high"] = None
    k_values, _, _ = kd_series(rows)
    # 視窗包含缺值日（index 12 起連續 9 個視窗）→ None
    assert all(k_values[i] is None for i in range(12, 21) if i < len(rows))
    assert k_values[11] is not None


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
            "open": 2400.0,
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


def test_bollinger_series_flat_prices_has_zero_width():
    closes = [100.0] * 25
    upper, mid, lower = bollinger_series(closes)
    assert upper[18] is None and mid[18] is None  # 不足 20 筆
    assert upper[19] == mid[19] == lower[19] == 100.0  # 無波動 → 三軌重合


def test_bollinger_series_known_value():
    # 前 19 筆 100、第 20 筆 120：mean=101, 母體變異 = (19*1 + 361)/20 = 19 → std=√19
    closes = [100.0] * 19 + [120.0]
    upper, mid, lower = bollinger_series(closes)
    std = 19 ** 0.5
    assert abs(mid[19] - 101.0) < 1e-9
    assert abs(upper[19] - (101.0 + 2 * std)) < 1e-9
    assert abs(lower[19] - (101.0 - 2 * std)) < 1e-9
