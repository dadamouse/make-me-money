"""全市場融資維持率：TWSE 彙總解析、快照入表、盤前導航顯示與分級解讀。"""
from test_bot import CREDIT_SUMMARY, BotRuntime


def test_credit_financing_amount_parsing():
    from app.snapshot import credit_financing_amount

    assert credit_financing_amount(CREDIT_SUMMARY) == 300_000_000.0
    assert credit_financing_amount({"stat": "查無資料"}) is None
    assert credit_financing_amount({"stat": "OK", "tables": []}) is None


def test_daily_snapshot_stores_market_margin():
    with BotRuntime() as rt:
        response = rt.client.post("/admin/daily-snapshot", headers={"x-cron-secret": "cron-secret"})
        assert response.status_code == 200
        assert response.json()["market_margin"] == 1
        rows = rt.postgrest.db["market_margin"]
        assert len(rows) == 1
        assert rows[0]["trade_date"] == "2026-07-02"  # 上市收盤資料日
        assert rows[0]["financing_amount"] == 300_000_000.0
        assert rows[0]["collateral_value"] == 501_000_000.0
        assert rows[0]["maintenance_pct"] == 167.0  # 501,000,000 / 300,000,000


def test_margin_maintenance_note_bands():
    from app.premarket import margin_maintenance_note

    assert "斷頭" in margin_maintenance_note(149.9)
    assert "追繳" in margin_maintenance_note(155.0)
    assert margin_maintenance_note(165.0) is None  # 160–170 中性不解讀
    assert "安全" in margin_maintenance_note(172.0)


def test_morning_open_shows_margin_maintenance():
    with BotRuntime() as rt:
        rt.send("登入dada", line_user_id="U-dada")
        rt.postgrest.db["market_margin"] += [
            {"trade_date": "2026-07-07", "maintenance_pct": 155.3},
            {"trade_date": "2026-07-06", "maintenance_pct": 158.0},
        ]
        rt.client.post("/admin/morning-open", headers={"x-cron-secret": "cron-secret"})
        text = rt.replies[-1]["messages"][0]["text"]
        assert "融資維持率 155.3%（前日 158.0%）" in text
        assert "追繳壓力浮現" in text
