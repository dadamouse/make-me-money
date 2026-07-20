"""買進檢查（買2330）：附圖 flex、逐項詳細說明、有利/風險分欄。"""
import json

from test_bot import BotRuntime, _seed_history


def test_buy_check_flex_with_chart_and_details():
    with BotRuntime() as rt:
        rt.send("登入dada")
        _seed_history(rt, "2330")
        rt.send("買2330")
        message = rt.last_message()
        assert message["type"] == "flex"
        assert "買進檢查" in message["altText"]
        bubble = message["contents"]
        assert bubble["hero"]["url"].startswith("http://testserver/charts/")  # 圖也要給
        content = json.dumps(bubble, ensure_ascii=False)
        assert "有利" in content
        assert "風險" in content or "注意" in content
        # 每一項都要有詳細說明（白話 why），抽查幾個代表性內容
        assert "月線" in content and "平均成本" in content  # MA20 的詳細說明
        assert "扣抵" in content
        assert "支撐跌破法" in content  # 訊號區
        assert "大盤" in content  # 環境面
        assert "非投資建議" in content


def test_buy_check_by_name_and_unknown():
    with BotRuntime() as rt:
        rt.send("登入dada")
        _seed_history(rt, "2330")
        rt.send("買台積電")
        assert "買進檢查" in rt.last_message().get("altText", "")
        rt.send("買不存在的")
        assert "找不到" in rt.last_reply()


def test_buy_check_insufficient_history():
    with BotRuntime() as rt:
        rt.send("登入dada")
        rt.send("買2330")  # 無歷史資料
        assert "資料不足" in rt.last_reply()


def test_sell_check_flex_with_sell_signals():
    with BotRuntime() as rt:
        rt.send("登入dada")
        _seed_history(rt, "2330")
        rt.send("賣2330")
        message = rt.last_message()
        assert message["type"] == "flex"
        assert "賣出檢查" in message["altText"]
        bubble = message["contents"]
        assert bubble["hero"]["url"].startswith("http://testserver/charts/")
        content = json.dumps(bubble, ensure_ascii=False)
        assert "續抱理由" in content
        assert "出場警訊" in content
        assert "S1" in content and "S5" in content  # 賣訊逐項列出
        assert "非投資建議" in content


def test_sell_check_unknown_stock():
    with BotRuntime() as rt:
        rt.send("登入dada")
        rt.send("賣不存在的")
        assert "找不到" in rt.last_reply()


def test_parse_revenue_mix():
    from app.buy_check import parse_revenue_mix

    html = """<table><tr><td>營收比重</td><td>晶圓-5奈米30.98%、晶圓-3奈米20.85% (2025年)</td></tr></table>"""
    assert parse_revenue_mix(html) == "晶圓-5奈米30.98%、晶圓-3奈米20.85% (2025年)"
    assert parse_revenue_mix("<table><tr><td>其他</td></tr></table>") is None


def test_stock_news_flex_titles_as_buttons_max_10():
    """新聞2330：Flex 卡、標題可點開連結、網址不露出、最多 10 則（來源 RSS 有 12 則）。"""
    with BotRuntime() as rt:
        rt.send("登入dada")
        rt.send("新聞2330")
        message = rt.last_message()
        assert message["type"] == "flex"
        content = json.dumps(message["contents"], ensure_ascii=False)
        assert "📰 台積電（2330）相關新聞" in content
        assert "新聞標題1" in content and "新聞標題10" in content
        assert "新聞標題11" not in content  # 上限 10
        assert '"uri": "https://news.google.com/a1"' in content  # 連結藏在動作裡
        assert '"text": "https://' not in content  # 不把網址印出來


def test_buy_check_card_has_news_button():
    with BotRuntime() as rt:
        rt.send("登入dada")
        _seed_history(rt, "2330")
        rt.send("買2330")
        content = json.dumps(rt.last_message()["contents"], ensure_ascii=False)
        assert '"text": "新聞2330"' in content  # 📰 按鈕自動送出新聞指令


def test_bare_news_keyword_still_menu3():
    """單獨打「新聞」仍是選單 3 的持股今日資訊，不是個股新聞。"""
    with BotRuntime() as rt:
        rt.send("登入dada")
        rt.send("新聞")
        reply = rt.last_reply()
        assert "相關新聞" not in reply  # 不是個股新聞格式
        assert "沒有任何持股" in reply  # 走選單 3 的持股訊息流程（測試帳號無持股）


def test_buy_check_sector_momentum_and_peers():
    """類股輪動：強勢類股列有利＋相關性自動配對的同業 reference。"""
    with BotRuntime() as rt:
        rt.send("登入dada")
        _seed_history(rt, "2330")
        rt.send("買2330")
        content = json.dumps(rt.last_message()["contents"], ensure_ascii=False)
        assert "類股動能" in content
        assert "34 類中第 2 名" in content
        assert "同業比較（近 5 日累計）：聯發科 +6.2%（連動 0.72）、日月光投控 +3.9%（連動 0.66）" in content
