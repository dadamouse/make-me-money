from test_bot import BotRuntime


def test_morning_macro_endpoint_is_merged_noop():
    """總經快報已併入盤前導航：舊端點保留但不推播（舊 pg_cron 打進來不會重複發）。"""
    with BotRuntime() as rt:
        rt.send("登入dada")
        pushes_before = len(rt.replies)
        response = rt.client.post("/admin/morning-macro", headers={"x-cron-secret": "cron-secret"})
        assert response.status_code == 200
        assert response.json() == {"ok": True, "pushed": 0, "skipped": "merged into morning-open"}
        assert len(rt.replies) == pushes_before

        assert rt.client.post("/admin/morning-macro", headers={"x-cron-secret": "wrong"}).status_code == 403


def test_morning_open_push_merged_brief():
    with BotRuntime() as rt:
        rt.send("登入dada")
        rt.postgrest.db["daily_closes"] += [
            {"stock_no": "2330", "trade_date": "2026-07-06", "close": 2445.0},
            {"stock_no": "0050", "trade_date": "2026-07-06", "close": 108.25},
            {"stock_no": "0050", "trade_date": "2026-07-03", "close": 108.35},
        ]
        response = rt.client.post("/admin/morning-open", headers={"x-cron-secret": "cron-secret"})
        assert response.status_code == 200
        assert response.json()["pushed"] == 1
        text = rt.replies[-1]["messages"][0]["text"]
        assert "盤前導航" in text
        # 總經區塊（原 8:00 總經快報內容）
        assert "【隔夜國際市場】" in text
        assert "道瓊 53,055.91　🔺0.3%" in text
        assert "日經 225 68,256.96　🔽2.1%" in text
        assert "KOSPI" not in text  # 韓股與台股連動低，已移除
        assert "【台股連動指標】" in text
        assert "台積電 ADR 451.79　🔺4.1%" in text
        assert "美元/台幣 32.000　🔺0.31%（台幣貶）" in text
        # ADR 隱含價換算已移除（常態溢價 15-25%，絕對換算無預測意義）
        assert "隱含" not in text
        assert "≈" not in text
        assert "【台股試撮（8:30-9:00 模擬撮合）】" in text
        assert "加權指數 45,479.11（-2.3%，08:35:00）" in text
        assert "台積電 2,440（-0.8%，08:35:00）" in text
        assert "＊8:55 前試撮可掛假單" in text
        assert "0050 收 108.25（-0.1%）" in text
        assert "三大法人 -242,259 張" in text
        assert "融資增減 -42,283 張" in text
        assert "今日無除權息" in text
        # 白話解讀：總經＋試撮＋法人融資合併在同一段
        assert "【📖 白話解讀】" in text
        assert "美股科技股" in text
        assert "日經" in text
        assert "台積電 ADR 漲 4.1% → 台積電今天大概率開高" in text
        assert "台幣明顯走貶 → 外資資金偏流出" in text
        assert "→ 總結：" in text
        assert "試撮指數 -2.3% → 開盤預告重挫" in text
        assert "昨日法人合計賣超 → 大戶偏保守" in text
        assert "融資減少 → 散戶槓桿退場中" in text
        assert text.count("【📖 白話解讀】") == 1


# conftest 固定「今天」為 2026-07-10（民國 1150710 週五）
_HOLIDAY_TODAY = [{"Name": "測試假日", "Date": "1150710", "Weekday": "五", "Description": ""}]


def test_morning_open_skipped_on_scheduled_holiday():
    """國定休市日（TWSE 休市日曆）：盤前導航不推播。"""
    with BotRuntime(holiday_fixture=_HOLIDAY_TODAY) as rt:
        rt.send("登入dada")
        pushes_before = len(rt.replies)
        response = rt.client.post("/admin/morning-open", headers={"x-cron-secret": "cron-secret"})
        assert response.status_code == 200
        assert response.json() == {"ok": True, "pushed": 0, "skipped": "market closed"}
        assert len(rt.replies) == pushes_before


def test_morning_open_skipped_when_mis_date_stale():
    """颱風臨時休市：MIS 資料日停在前一交易日 → 整則跳過（總經已併入，早上完全安靜）。"""
    mis = {
        "t00": {"c": "t00", "n": "發行量加權股價指數", "z": "-", "y": "46556.39",
                "t": "13:33:00", "d": "20250101"},  # 固定用過去日期，永遠 != 今天
    }
    with BotRuntime(mis_fixtures=mis) as rt:
        rt.send("登入dada")
        pushes_before = len(rt.replies)
        response = rt.client.post("/admin/morning-open", headers={"x-cron-secret": "cron-secret"})
        assert response.status_code == 200
        assert response.json() == {"ok": True, "pushed": 0, "skipped": "stale mis date"}
        response = rt.client.post("/admin/morning-macro", headers={"x-cron-secret": "cron-secret"})
        assert response.json()["pushed"] == 0
        assert len(rt.replies) == pushes_before


def test_morning_open_pushes_even_without_trial_price():
    """z 是瞬時欄位、盤中也常缺值：MIS 沒回 z（甚至整包沒回）也要照發，試撮段顯示暫缺。
    曾因把 z 當休市開關而連日漏發（2026-07 中旬）。"""
    with BotRuntime(mis_fixtures={}) as rt:
        rt.send("登入dada")
        response = rt.client.post("/admin/morning-open", headers={"x-cron-secret": "cron-secret"})
        assert response.status_code == 200
        assert response.json()["pushed"] == 1
        text = rt.replies[-1]["messages"][0]["text"]
        assert "【隔夜國際市場】" in text  # 總經照發
        assert "試撮價暫缺" in text
        assert "＊8:55 前試撮可掛假單" not in text


def test_fetch_quote_flags_data_date():
    """亞股指數的 is_today：最後一根有效日線是不是該市場的今天。"""
    import asyncio
    import time

    import httpx as _httpx

    from app.premarket import fetch_quote

    def run(last_ts):
        def handler(request):
            return _httpx.Response(200, json={"chart": {"result": [{
                "meta": {"gmtoffset": 32400},  # JST
                "timestamp": [last_ts - 86400, last_ts],
                "indicators": {"quote": [{"close": [100.0, 101.0]}]},
            }]}})

        async def go():
            async with _httpx.AsyncClient(transport=_httpx.MockTransport(handler)) as http:
                return await fetch_quote(http, "^N225")

        return asyncio.run(go())

    now = time.time()
    assert run(now)["is_today"] is True
    assert run(now - 86400)["is_today"] is False


def test_fetch_quote_without_meta_is_unknown():
    """BotRuntime 這類沒給 meta/timestamp 的來源 → is_today None，不加標註。"""
    import asyncio

    import httpx as _httpx

    from app.premarket import fetch_quote

    def handler(request):
        return _httpx.Response(200, json={"chart": {"result": [{
            "indicators": {"quote": [{"close": [100.0, 101.0]}]},
        }]}})

    async def go():
        async with _httpx.AsyncClient(transport=_httpx.MockTransport(handler)) as http:
            return await fetch_quote(http, "^N225")

    assert asyncio.run(go())["is_today"] is None
