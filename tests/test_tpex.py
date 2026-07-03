from app.parser import format_roc_compact, parse_tpex_close


def test_format_roc_compact():
    assert format_roc_compact("1150703") == "115/07/03"
    assert format_roc_compact("") == ""
    assert format_roc_compact(None) == ""


def test_parse_tpex_close_finds_stock():
    quotes = [
        {"Date": "1150702", "SecuritiesCompanyCode": "006201", "Close": "49.30"},
        {"Date": "1150702", "SecuritiesCompanyCode": "5274", "Close": "5,000.00"},
    ]
    assert parse_tpex_close(quotes, "5274") == {"date": "115/07/02", "close": 5000.0}


def test_parse_tpex_close_handles_missing_or_invalid():
    assert parse_tpex_close([], "5274") is None
    assert parse_tpex_close(None, "5274") is None
    assert parse_tpex_close([{"Date": "1150702", "SecuritiesCompanyCode": "5274", "Close": "--"}], "5274") is None
