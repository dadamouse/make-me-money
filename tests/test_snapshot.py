from app.parser import roc_compact_to_iso
from app.snapshot import listed_close_rows, otc_close_rows


def test_roc_compact_to_iso():
    assert roc_compact_to_iso("1150702") == "2026-07-02"
    assert roc_compact_to_iso("") is None
    assert roc_compact_to_iso("abc") is None
    assert roc_compact_to_iso(None) is None


def test_listed_close_rows_parses_and_skips_invalid():
    quotes = [
        {"Code": "2330", "Name": "台積電", "Date": "1150702", "ClosingPrice": "2,465.00"},
        {"Code": "9999", "Name": "停牌股", "Date": "1150702", "ClosingPrice": "--"},
        {"Code": "", "Name": "怪資料", "Date": "1150702", "ClosingPrice": "10"},
    ]
    assert listed_close_rows(quotes) == [{"stock_no": "2330", "trade_date": "2026-07-02", "close": 2465.0}]


def test_otc_close_rows():
    quotes = [{"Date": "1150702", "SecuritiesCompanyCode": "5274", "Close": "5000.00"}]
    assert otc_close_rows(quotes) == [{"stock_no": "5274", "trade_date": "2026-07-02", "close": 5000.0}]
