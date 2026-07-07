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
        assert "日經 225 68,256.96　🔻2.1%" in text
        assert "韓國 KOSPI 7,656.31　🔻4.9%" in text
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
        # 451.79 × 32.0 ÷ 5 = 2891.456
        assert "台積電 ADR 451.79 美元 × 匯率 32.00 ÷ 5 ≈ 2,891.46 元" in text
        assert "對昨收 2,445：+18.3%" in text
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
        assert "ADR 隱含台積電開高約 18.3%" in text
        assert "昨日法人合計賣超 → 大戶偏保守" in text
        assert "融資減少 → 散戶槓桿退場中" in text
