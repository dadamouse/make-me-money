from datetime import date, datetime, timedelta, timezone

from app.weekly import upcoming_deadlines

from test_bot import BotRuntime  # noqa: E402

_TAIPEI_TZ = timezone(timedelta(hours=8))


def test_upcoming_deadlines():
    assert upcoming_deadlines(date(2026, 5, 10)) == ["5/15 Q1 財報申報期限"]
    assert upcoming_deadlines(date(2026, 8, 8)) == ["8/10 各公司8月營收公布期限", "8/14 Q2 財報申報期限"]
    assert upcoming_deadlines(date(2026, 6, 15)) == []


def _today() -> date:
    return datetime.now(_TAIPEI_TZ).date()


def test_weekly_report_push():
    with BotRuntime() as rt:
        rt.send("登入dada")
        rt.send("新增2330 1000 850")
        today = _today()
        for offset, close in ((6, 100.0), (1, 110.0)):
            rt.postgrest.db["daily_closes"].append(
                {"stock_no": "2330", "trade_date": (today - timedelta(days=offset)).isoformat(), "close": close}
            )
        rt.postgrest.db["daily_institutional"].append(
            {"stock_no": "2330", "trade_date": (today - timedelta(days=2)).isoformat(),
             "foreign_net": 5000000, "trust_net": -1000000}
        )
        response = rt.client.post("/admin/weekly-report", headers={"x-cron-secret": "cron-secret"})
        assert response.status_code == 200
        assert response.json()["pushed"] == 1
        push = rt.replies[-1]
        assert push["to"] == "U-test"
        text = push["messages"][0]["text"]
        assert "dada 的持股週報" in text
        assert "2330 台積電：100 → 110（+10.0%）" in text
        assert "總市值 110,000（本週 +10.0%）" in text
        assert "【法人本週動向】" in text
        assert "外資 +5,000 張｜投信 -1,000 張" in text

        assert rt.client.post("/admin/weekly-report", headers={"x-cron-secret": "wrong"}).status_code == 403


def test_weekly_outlook_push_and_backfill():
    with BotRuntime() as rt:
        rt.send("登入dada")
        rt.send("新增2330")
        rt.postgrest.db["dividend_events"].append(
            {"stock_no": "2330", "ex_date": (_today() + timedelta(days=3)).isoformat(),
             "kind": "息", "cash_dividend": 5.0}
        )
        response = rt.client.post("/admin/weekly-outlook", headers={"x-cron-secret": "cron-secret"})
        assert response.status_code == 200
        payload = response.json()
        assert payload["pushed"] == 1
        assert "backfill" in payload
        text = rt.replies[-1]["messages"][0]["text"]
        assert "dada 的下週展望" in text
        assert "除息，現金股利 5 元" in text


def test_weekly_report_skips_members_without_holdings():
    with BotRuntime() as rt:
        rt.send("登入dada")  # 綁定但沒有持股
        response = rt.client.post("/admin/weekly-report", headers={"x-cron-secret": "cron-secret"})
        assert response.json()["pushed"] == 0
