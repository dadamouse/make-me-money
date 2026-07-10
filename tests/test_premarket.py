from test_bot import BotRuntime


def test_morning_macro_push():
    with BotRuntime() as rt:
        rt.send("登入dada")
        response = rt.client.post("/admin/morning-macro", headers={"x-cron-secret": "cron-secret"})
        assert response.status_code == 200
        assert response.json()["pushed"] == 1
        text = rt.replies[-1]["messages"][0]["text"]
        assert "盤前總經快報" in text
        assert "道瓊 53,055.91　🔺0.3%" in text
        assert "日經 225 68,256.96　🔽2.1%" in text
        assert "韓國 KOSPI 7,656.31　🔽4.9%" in text
        assert "台積電 ADR 451.79　🔺4.1%" in text
        assert "美元/台幣 32.000　🔺0.31%（台幣貶）" in text
        # 白話解讀：教你怎麼看
        assert "【📖 白話解讀】" in text
        assert "美股科技股" in text
        assert "日韓股市" in text
        assert "台積電 ADR 漲 4.1% → 台積電今天大概率開高" in text
        assert "台幣明顯走貶 → 外資資金偏流出" in text
        assert "→ 總結：" in text

        assert rt.client.post("/admin/morning-macro", headers={"x-cron-secret": "wrong"}).status_code == 403


def test_morning_open_push_with_adr_implied():
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
        assert "開盤前導航" in text
        # ADR 隱含價換算已移除（常態溢價 15-25%，絕對換算無預測意義）
        assert "隱含" not in text
        assert "≈" not in text
        assert "【台股試撮（8:30-9:00 模擬撮合）】" in text
        assert "加權指數 45,479.11（-2.3%，08:35:00）" in text
        assert "台積電 2,440（-0.8%，08:35:00）" in text
        assert "＊8:55 前試撮可掛假單" in text
        assert "試撮指數 -2.3% → 開盤預告重挫" in text
        assert "0050 收 108.25（-0.1%）" in text
        assert "三大法人 -242,259 張" in text
        assert "融資增減 -42,283 張" in text
        assert "今日無除權息" in text
        # 白話解讀
        assert "【📖 白話解讀】" in text
        assert "試撮指數 -2.3% → 開盤預告重挫" in text
        assert "昨日法人合計賣超 → 大戶偏保守" in text
        assert "融資減少 → 散戶槓桿退場中" in text


# conftest 固定「今天」為 2026-07-10（民國 1150710 週五）
_HOLIDAY_TODAY = [{"Name": "測試假日", "Date": "1150710", "Weekday": "五", "Description": ""}]


def test_morning_pushes_skipped_on_scheduled_holiday():
    """國定休市日（TWSE 休市日曆）：早上兩則都不推播。"""
    with BotRuntime(holiday_fixture=_HOLIDAY_TODAY) as rt:
        rt.send("登入dada")
        pushes_before = len(rt.replies)
        for endpoint in ("/admin/morning-macro", "/admin/morning-open"):
            response = rt.client.post(endpoint, headers={"x-cron-secret": "cron-secret"})
            assert response.status_code == 200
            assert response.json() == {"ok": True, "pushed": 0, "skipped": "market closed"}
        assert len(rt.replies) == pushes_before


def test_morning_open_skipped_without_trial_data():
    """颱風臨時休市不在休市日曆內：8:30 平日應有試撮價，完全沒有就跳過開盤導航。"""
    with BotRuntime(mis_fixtures={}) as rt:
        rt.send("登入dada")
        pushes_before = len(rt.replies)
        response = rt.client.post("/admin/morning-open", headers={"x-cron-secret": "cron-secret"})
        assert response.status_code == 200
        assert response.json() == {"ok": True, "pushed": 0, "skipped": "no trial data"}
        assert len(rt.replies) == pushes_before
        # 總經快報以國外資料為主，颱風天 8:00 無可靠休市訊號，仍照常發（已知限制）
        response = rt.client.post("/admin/morning-macro", headers={"x-cron-secret": "cron-secret"})
        assert response.json()["pushed"] == 1
