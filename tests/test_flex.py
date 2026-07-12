import json

from app.flex import GAIN_COLOR, LOSS_COLOR, build_portfolio_message

TSMC = {
    "stock_no": "2330", "name": "台積電", "industry": "半導體",
    "shares": 1000, "cost": 850000, "quote": {"date": "115/07/02", "close": 2355},
}
WISTRON_WATCHING = {
    "stock_no": "3231", "name": "緯創", "industry": "電腦及週邊設備",
    "shares": 0, "cost": 0, "quote": {"date": "115/07/02", "close": 100},
}
NO_QUOTE = {"stock_no": "6488", "name": "環球晶", "shares": 100, "cost": 0, "quote": None}


def dump(message: dict) -> str:
    return json.dumps(message["contents"], ensure_ascii=False)


def test_flex_message_structure_and_content():
    message = build_portfolio_message("dada", [TSMC, WISTRON_WATCHING])
    assert message["type"] == "flex"
    assert "dada 的持股" in message["altText"]
    bubble = message["contents"]
    assert bubble["type"] == "bubble"
    assert "收盤日 07/02" in dump(message)
    content = dump(message)
    assert "2330 台積電" in content
    assert "市值 2,355,000" in content
    assert "+1,505,000（+177.1%）" in content
    assert "觀察中（未記股數）" in content
    assert "總市值" in content
    assert "半導體" in content
    assert "電腦及週邊設備" in content


def test_flex_unknown_industry_grouped_as_fallback():
    message = build_portfolio_message("dada", [NO_QUOTE])
    assert "其他" in dump(message)


def test_flex_gain_is_red_loss_is_green():
    gain = build_portfolio_message("dada", [TSMC])
    assert GAIN_COLOR in dump(gain)
    losing = {**TSMC, "cost": 3000000}
    loss = build_portfolio_message("dada", [losing])
    assert LOSS_COLOR in dump(loss)
    assert "-645,000（-21.5%）" in dump(loss)


def test_flex_without_quote_shows_placeholder_and_no_footer():
    message = build_portfolio_message("dada", [NO_QUOTE])
    assert "查無報價" in dump(message)
    assert "footer" not in message["contents"]


def test_flex_alt_text_is_truncated():
    entries = [{**TSMC, "stock_no": str(1000 + i)} for i in range(50)]
    message = build_portfolio_message("dada", entries)
    assert len(message["altText"]) <= 400


def test_flex_shows_macd_and_bollinger_row():
    entry = {
        **TSMC,
        "indicators": {"ma5": 2300.0, "ma20": 2250.0, "ma60": None, "rsi14": 62.0, "k": 70.0, "d": 65.0,
                       "j": 80.0, "macd_hist": 12.34, "percent_b": 0.87},
    }
    content = dump(build_portfolio_message("測試", [entry]))
    assert "MACD柱 +12.34" in content
    assert "布林 近上軌（%b 0.87）" in content


def test_flex_bollinger_labels():
    from app.flex import _bollinger_label

    assert _bollinger_label(1.05) == "衝出上軌"
    assert _bollinger_label(0.87) == "近上軌"
    assert _bollinger_label(0.6) == "通道中上"
    assert _bollinger_label(0.3) == "通道中下"
    assert _bollinger_label(0.05) == "近下軌"
    assert _bollinger_label(-0.1) == "跌破下軌"
