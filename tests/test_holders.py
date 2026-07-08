from app.holders import parse_holders_csv

from test_bot import BotRuntime

SAMPLE_CSV = "\n".join(
    [
        "資料日期,證券代號,持股分級,人數,股數,占集保庫存數比例%",
        "20260703,2330  ,1,2362549,272902764,1.05",
        "20260703,2330  ,15,1481,22066084295,85.09",
        "20260703,2330  ,17,2898020,25932370067,100.00",
        "20260703,3231  ,15,890,1200000000,42.50",
        "20260703,3231  ,17,350000,2800000000,100.00",
        "壞資料列",
    ]
)


def test_parse_holders_csv():
    rows = {r["stock_no"]: r for r in parse_holders_csv(SAMPLE_CSV)}
    assert rows["2330"] == {
        "stock_no": "2330",
        "week_date": "2026-07-03",
        "big_holders": 1481.0,
        "big_ratio": 85.09,
        "total_holders": 2898020.0,
    }
    assert rows["3231"]["big_ratio"] == 42.5


def test_sync_holders_endpoint():
    with BotRuntime() as rt:
        response = rt.client.post("/admin/sync-holders", headers={"x-cron-secret": "cron-secret"})
        assert response.status_code == 200
        payload = response.json()
        assert payload["ok"] is True
        assert payload["rows"] == 2
        assert payload["week"] == "2026-07-03"
        stored = {r["stock_no"]: r for r in rt.postgrest.db["weekly_holders"]}
        assert stored["2330"]["big_ratio"] == 85.09

        assert rt.client.post("/admin/sync-holders", headers={"x-cron-secret": "wrong"}).status_code == 403


def test_chart_card_shows_holders_line():
    with BotRuntime() as rt:
        from test_bot import _seed_history
        import json

        _seed_history(rt, "2330")
        rt.postgrest.db["weekly_holders"] += [
            {"stock_no": "2330", "week_date": "2026-07-03", "total_holders": 2898020, "big_holders": 1481, "big_ratio": 85.09},
            {"stock_no": "2330", "week_date": "2026-06-26", "total_holders": 2950000, "big_holders": 1450, "big_ratio": 84.80},
        ]
        rt.send("登入dada")
        rt.send("圖2330")
        content = json.dumps(rt.last_message()["contents"], ensure_ascii=False)
        assert "千張大戶 85.1%（週+0.29pp）｜股東 2,898,020 人（週-1.8%）" in content


def test_concentration_strategy_skipped_until_two_weeks():
    with BotRuntime(rpc_overrides={"holders_depth": [{"weeks": 1}]}) as rt:
        rt.send("選股")
        import json

        content = json.dumps(rt.last_message()["contents"], ensure_ascii=False)
        assert "籌碼集中（週）" in content
        assert "資料累積中（需 2 週，目前 1）" in content


def test_concentration_strategy_picks():
    with BotRuntime() as rt:
        rt.send("選股")
        import json

        content = json.dumps(rt.last_message()["contents"], ensure_ascii=False)
        assert "千張大戶 45.2%（週+1.20pp）、股東數週 -2.3%" in content
