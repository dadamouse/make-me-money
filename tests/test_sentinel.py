"""哨兵測試：盤後持股警訊、大盤警報、盤中急跌、同日去重。"""
import json

from test_bot import BotRuntime

# 大盤序列（新到舊）：今日 45479 跌破月線且單日 -2.3%；昨日 46556 在月線上 → 兩則警報
MARKET_CROSS = [
    {"trade_date": "2026-07-20", "taiex": 45479.11, "amount": 10e12},
    {"trade_date": "2026-07-17", "taiex": 46556.39, "amount": 10e12},
] + [{"trade_date": f"2026-06-{25 - i:02d}", "taiex": 46000.0, "amount": 10e12} for i in range(23)]


def _seed_trend_break(rt, stock_no: str, days: int = 60):
    """前 59 日每日 +1 緩漲，今日暴跌：S1（跌破5日線且下彎）與跌破月線今天新成立。"""
    rows = []
    for i in range(days - 1):
        close = 100.0 + i
        rows.append({
            "stock_no": stock_no, "trade_date": f"2026-{4 + i // 28:02d}-{i % 28 + 1:02d}",
            "close": close, "open": close - 0.5, "high": close + 1, "low": close - 1, "volume": 2_000_000,
        })
    rows.append({
        "stock_no": stock_no, "trade_date": "2026-07-20",
        "close": 140.0, "open": 158.0, "high": 158.5, "low": 139.0, "volume": 6_000_000,
    })
    rt.postgrest.db["daily_closes"] += rows


def test_close_sentinel_market_and_holdings_alerts():
    with BotRuntime(rpc_overrides={"market_series": MARKET_CROSS}) as rt:
        rt.send("登入dada", line_user_id="U-dada")
        rt.send("+2330 1000 850", line_user_id="U-dada")
        rt.send("登入gino", line_user_id="U-gino")  # 沒有持股 2330
        _seed_trend_break(rt, "2330")
        response = rt.client.post("/admin/sentinel", headers={"x-cron-secret": "cron-secret"})
        assert response.status_code == 200
        payload = response.json()
        assert payload["market_alerts"] >= 2  # 跌破月線＋單日重挫
        assert payload["personal_alerts"] == 1

        # 大盤警報 multicast 給所有人
        multicasts = [r for r in rt.replies if isinstance(r.get("to"), list)]
        assert multicasts, "應有大盤警報廣播"
        assert "跌破月線" in multicasts[-1]["messages"][0]["text"]
        assert "重挫" in multicasts[-1]["messages"][0]["text"]
        # 持股警訊只推給持有人 dada
        pushes = [r for r in rt.replies if r.get("to") == "U-dada" and "持股盤後警訊" in str(r["messages"])]
        assert pushes
        text = pushes[-1]["messages"][0]["text"]
        assert "S1" in text
        assert "跌破月線" in text
        assert not [r for r in rt.replies if r.get("to") == "U-gino" and "持股" in str(r["messages"])]


def test_sentinel_dedup_same_day():
    with BotRuntime(rpc_overrides={"market_series": MARKET_CROSS}) as rt:
        rt.send("登入dada", line_user_id="U-dada")
        first = rt.client.post("/admin/sentinel", headers={"x-cron-secret": "cron-secret"}).json()
        assert first["market_alerts"] >= 2
        second = rt.client.post("/admin/sentinel", headers={"x-cron-secret": "cron-secret"}).json()
        assert second["market_alerts"] == 0  # 同日重跑不重複推播
        assert second["personal_alerts"] == 0


def test_intraday_sentinel_drop_alerts():
    """MIS 即時：大盤 -2.3%（fixture 45479/46556）、2330 即時 2440 vs 昨收 2600 = -6.2%。"""
    with BotRuntime() as rt:
        rt.send("登入dada", line_user_id="U-dada")
        rt.send("+2330 1000 850", line_user_id="U-dada")
        rt.postgrest.db["daily_closes"].append(
            {"stock_no": "2330", "trade_date": "2026-07-19", "close": 2600.0, "volume": 1_000_000}
        )
        response = rt.client.post("/admin/sentinel?mode=intraday", headers={"x-cron-secret": "cron-secret"})
        payload = response.json()
        assert payload["market_alerts"] == 1
        assert payload["personal_alerts"] == 1
        pushes = [r for r in rt.replies if r.get("to") == "U-dada" and "盤中" in str(r["messages"])]
        assert "盤中跌 -6.2%" in pushes[-1]["messages"][0]["text"]


def test_sentinel_requires_secret():
    with BotRuntime() as rt:
        assert rt.client.post("/admin/sentinel", headers={"x-cron-secret": "wrong"}).status_code == 403


def test_daily_quote_rotation_and_pushes():
    """大師警語：庫存 ≥100 條無重複；盤前導航與晚間選股尾端各帶一條（不同條）。"""
    from app.quotes import QUOTES, daily_quote

    assert len(QUOTES) >= 100
    assert len(QUOTES) == len(set(QUOTES))
    assert daily_quote().startswith("💬 ")
    assert daily_quote() != daily_quote(offset=1)

    with BotRuntime() as rt:
        rt.send("登入dada", line_user_id="U-dada")
        response = rt.client.post("/admin/morning-open", headers={"x-cron-secret": "cron-secret"})
        assert response.json()["pushed"] == 1
        assert "💬 " in rt.replies[-1]["messages"][0]["text"]

        rt.postgrest.db["daily_closes"].append({"stock_no": "2330", "trade_date": "2026-07-10", "close": 2465.0})
        rt.client.post("/admin/daily-picks", headers={"x-cron-secret": "cron-secret"})
        push = rt.replies[-1]
        assert len(push["messages"]) == 2  # 名言併入健檢卡尾端，不佔獨立訊息物件
        health_json = json.dumps(push["messages"][0], ensure_ascii=False)
        assert "💬 " in health_json
