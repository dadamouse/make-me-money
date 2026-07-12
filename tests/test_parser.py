from app.parser import (
    Command,
    aggregate_holdings,
    format_number,
    format_portfolio,
    format_roc_date,
    parse_command,
    parse_twse_close,
)


def test_parse_login_with_and_without_space():
    assert parse_command("登入dada") == Command(action="login", name="dada")
    assert parse_command("登入 dada") == Command(action="login", name="dada")


def test_parse_switch():
    assert parse_command("切換媽媽") == Command(action="switch", name="媽媽")


def test_parse_add_code_only():
    assert parse_command("新增2330") == Command(action="add", stock="2330", shares=None, cost=None)


def test_parse_add_by_name():
    assert parse_command("新增 緯創") == Command(action="add", stock="緯創", shares=None, cost=None)


def test_parse_add_with_shares_and_cost():
    assert parse_command("新增2330 1000 850") == Command(action="add", stock="2330", shares=1000, cost=850)
    assert parse_command("新增2330 1000") == Command(action="add", stock="2330", shares=1000, cost=None)
    assert parse_command("新增2330 1000 850.5") == Command(action="add", stock="2330", shares=1000, cost=850.5)


def test_parse_remove():
    assert parse_command("刪除2330") == Command(action="remove", stock="2330")


def test_parse_add_shorthand_plus():
    assert parse_command("+2330 1000 850") == Command(action="add", stock="2330", shares=1000, cost=850)
    assert parse_command("+2330") == Command(action="add", stock="2330", shares=None, cost=None)
    assert parse_command("＋台積電") == Command(action="add", stock="台積電", shares=None, cost=None)  # 全形＋


def test_parse_remove_shorthand_minus():
    assert parse_command("-2330") == Command(action="remove", stock="2330")
    assert parse_command("－捷敏ky") == Command(action="remove", stock="捷敏ky")  # 全形－


def test_parse_list():
    assert parse_command("我的股票") == Command(action="list")
    assert parse_command("清單") == Command(action="list")


def test_parse_unknown_falls_back_to_help():
    assert parse_command("哈囉").action == "help"
    assert parse_command("").action == "help"
    assert parse_command("新增2330 abc").action == "help"


def test_parse_twse_close_takes_last_row_and_strips_commas():
    api = {
        "stat": "OK",
        "data": [
            ["115/06/01", "60,942,792", "…", "2,355.00", "2,415.00", "2,350.00", "2,355.00", "0.00", "136,367"],
            ["115/06/02", "50,000,000", "…", "2,360.00", "2,420.00", "2,355.00", "2,400.00", "+45.00", "120,000"],
        ],
    }
    assert parse_twse_close(api) == {"date": "115/06/02", "close": 2400}


def test_parse_twse_close_skips_dash_rows():
    api = {
        "stat": "OK",
        "data": [
            ["115/06/01", "1", "1", "10", "10", "10", "10.50", "0", "1"],
            ["115/06/02", "0", "0", "--", "--", "--", "--", " 0.00", "0"],
        ],
    }
    assert parse_twse_close(api) == {"date": "115/06/01", "close": 10.5}


def test_parse_twse_close_returns_none_when_not_ok():
    assert parse_twse_close({"stat": "查詢日期大於今日，請重新查詢!", "total": 0}) is None
    assert parse_twse_close({"stat": "OK", "data": []}) is None
    assert parse_twse_close(None) is None


def test_format_roc_date():
    assert format_roc_date("115/07/02") == "07/02"
    assert format_roc_date("bad") == "bad"


def test_format_number():
    assert format_number(2355000) == "2,355,000"
    assert format_number(850.5) == "850.5"
    assert format_number(0.25) == "0.25"


def test_aggregate_holdings_groups_by_stock():
    rows = [
        {"stock_no": "2330", "shares": 1000, "cost_price": 850},
        {"stock_no": "2330", "shares": 500, "cost_price": 900},
        {"stock_no": "3231", "shares": 0, "cost_price": None},
    ]
    aggregated = {a["stock_no"]: a for a in aggregate_holdings(rows)}
    assert len(aggregated) == 2
    assert aggregated["2330"]["shares"] == 1500
    assert aggregated["2330"]["cost"] == 1000 * 850 + 500 * 900
    assert aggregated["3231"]["shares"] == 0
    assert aggregated["3231"]["cost"] == 0


def test_format_portfolio_with_pnl_watching_and_totals():
    text = format_portfolio(
        "dada",
        [
            {"stock_no": "2330", "name": "台積電", "shares": 1000, "cost": 850000, "quote": {"date": "115/07/02", "close": 2355}},
            {"stock_no": "3231", "name": "緯創", "shares": 0, "cost": 0, "quote": {"date": "115/07/02", "close": 100}},
        ],
    )
    assert "dada 的持股" in text
    assert "2330 台積電　收盤 2,355（07/02）" in text
    assert "1,000 股｜市值 2,355,000｜損益 +1,505,000（+177.1%）" in text
    assert "觀察中（未記股數）" in text
    assert "總市值 2,355,000｜總損益 +1,505,000" in text


def test_format_portfolio_warns_when_no_quote():
    text = format_portfolio("dada", [{"stock_no": "6488", "name": "環球晶", "shares": 100, "cost": 0, "quote": None}])
    assert "6488 環球晶　⚠️ 查無報價" in text
