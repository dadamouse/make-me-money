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
