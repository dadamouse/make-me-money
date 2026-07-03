from app.parser import roc_compact_to_iso
from app.snapshot import (
    listed_close_rows,
    listed_dividend_rows,
    listed_margin_rows,
    otc_close_rows,
    otc_dividend_rows,
    otc_margin_rows,
)


def test_roc_compact_to_iso():
    assert roc_compact_to_iso("1150702") == "2026-07-02"
    assert roc_compact_to_iso("") is None
    assert roc_compact_to_iso("abc") is None
    assert roc_compact_to_iso(None) is None


def test_listed_close_rows_with_ohlcv_and_skips_invalid():
    quotes = [
        {
            "Code": "2330", "Date": "1150702", "ClosingPrice": "2,465.00", "TradeVolume": "31,058,614",
            "OpeningPrice": "2,400.00", "HighestPrice": "2,470.00", "LowestPrice": "2,390.00",
        },
        {"Code": "9999", "Date": "1150702", "ClosingPrice": "--", "TradeVolume": "0"},
        {"Code": "", "Date": "1150702", "ClosingPrice": "10", "TradeVolume": "1"},
    ]
    assert listed_close_rows(quotes) == [
        {
            "stock_no": "2330", "trade_date": "2026-07-02", "close": 2465.0, "volume": 31058614.0,
            "open": 2400.0, "high": 2470.0, "low": 2390.0,
        }
    ]


def test_otc_close_rows_with_ohlcv():
    quotes = [
        {
            "Date": "1150702", "SecuritiesCompanyCode": "5274", "Close": "5000.00", "TradingShares": "216,609",
            "Open": "4,950.00", "High": "5,100.00", "Low": "4,900.00",
        }
    ]
    assert otc_close_rows(quotes) == [
        {
            "stock_no": "5274", "trade_date": "2026-07-02", "close": 5000.0, "volume": 216609.0,
            "open": 4950.0, "high": 5100.0, "low": 4900.0,
        }
    ]


def test_listed_margin_rows_computes_change():
    data = [
        {"股票代號": "2330", "融資今日餘額": "25,000", "融資前日餘額": "24,000", "融券今日餘額": "500", "融券前日餘額": "600"},
        {"股票代號": "", "融資今日餘額": "1"},
    ]
    rows = listed_margin_rows(data, "2026-07-02")
    assert rows == [
        {
            "stock_no": "2330",
            "trade_date": "2026-07-02",
            "margin_balance": 25000.0,
            "margin_change": 1000.0,
            "short_balance": 500.0,
            "short_change": -100.0,
        }
    ]
    assert listed_margin_rows(data, None) == []


def test_otc_margin_rows():
    data = [
        {
            "Date": "1150703",
            "SecuritiesCompanyCode": "5274",
            "MarginPurchaseBalance": "1200",
            "MarginPurchaseBalancePreviousDay": "1000",
            "ShortSaleBalance": "10",
            "ShortSaleBalancePreviousDay": "15",
        }
    ]
    rows = otc_margin_rows(data)
    assert rows[0]["margin_change"] == 200.0
    assert rows[0]["short_change"] == -5.0
    assert rows[0]["trade_date"] == "2026-07-03"


def test_listed_dividend_rows():
    data = [{"Date": "1150709", "Code": "2330", "Exdividend": "息", "CashDividend": "5.00", "StockDividendRatio": ""}]
    assert listed_dividend_rows(data) == [
        {
            "stock_no": "2330",
            "ex_date": "2026-07-09",
            "kind": "息",
            "cash_dividend": 5.0,
            "stock_dividend_ratio": None,
        }
    ]


def test_otc_dividend_rows_normalizes_kind():
    data = [
        {
            "ExRrightsExDividendDate": "1150710",
            "SecuritiesCompanyCode": "5274",
            "ExRrightsExDividend": "除息",
            "CashDividend": "3.50000000",
            "StockDividendRatio": "0.00000000",
        }
    ]
    rows = otc_dividend_rows(data)
    assert rows[0]["kind"] == "息"
    assert rows[0]["ex_date"] == "2026-07-10"
    assert rows[0]["cash_dividend"] == 3.5
