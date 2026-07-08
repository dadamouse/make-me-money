import json

from app.broker_flows import broker_flow_text, parse_zco

from test_bot import BotRuntime, _seed_history

ZCO_HTML = """
<html><body>
<td class="t10">台積電(2330) 券商分點-進出明細 <div class="t11">單位：張　最後更新日：2026/07/07</div></td>
<TR><TD class="t2" nowrap>買超券商</TD><TD>買進</TD><TD>賣出</TD><TD>買超</TD><TD>佔成交比重</TD>
<TD class="t2" nowrap>賣超券商</TD><TD>買進</TD><TD>賣出</TD><TD>賣超</TD><TD>佔成交比重</TD></TR>
<TR><TD><a href="#">新加坡商瑞銀</a></TD><TD>5,960</TD><TD>1,493</TD><TD>4,467</TD><TD>14.71%</TD>
<TD><a href="#">台灣摩根士丹利</a></TD><TD>483</TD><TD>2,107</TD><TD>1,624</TD><TD>5.35%</TD></TR>
<TR><TD><a href="#">花旗環球</a></TD><TD>3,642</TD><TD>1,269</TD><TD>2,373</TD><TD>7.81%</TD>
<TD><a href="#">元大證券</a></TD><TD>1,412</TD><TD>2,358</TD><TD>946</TD><TD>3.12%</TD></TR>
</body></html>
"""


def test_parse_zco():
    result = parse_zco(ZCO_HTML, "2330")
    assert result == {
        "stock_no": "2330",
        "trade_date": "2026-07-07",
        "top_buy_lots": 4467.0 + 2373.0,
        "top_sell_lots": 1624.0 + 946.0,
        "net_lots": 4270.0,
        "concentration_pct": 14.05,  # (14.71+7.81) - (5.35+3.12)
    }
    assert parse_zco("<html>沒有資料</html>", "2330") is None


def test_broker_flow_text():
    assert broker_flow_text({"net_lots": 752, "concentration_pct": 2.44}) == "主力 +752 張（集中 +2.4%）"
    assert broker_flow_text({"net_lots": -1200, "concentration_pct": -3.5}) == "主力 -1,200 張（集中 -3.5%）"
    assert broker_flow_text(None) is None


def test_sync_broker_flows_endpoint():
    with BotRuntime() as rt:
        rt.send("登入dada")
        rt.send("新增2330 1000 850")
        response = rt.client.post("/admin/sync-broker-flows", headers={"x-cron-secret": "cron-secret"})
        assert response.status_code == 200
        payload = response.json()
        assert payload == {"ok": True, "stocks": 1, "rows": 1}
        stored = rt.postgrest.db["daily_broker_flows"][0]
        assert stored["stock_no"] == "2330"
        assert stored["net_lots"] == 4270.0

        assert rt.client.post("/admin/sync-broker-flows", headers={"x-cron-secret": "wrong"}).status_code == 403


def test_portfolio_card_shows_broker_flow():
    with BotRuntime() as rt:
        _seed_history(rt, "2330")
        rt.postgrest.db["daily_broker_flows"].append(
            {"stock_no": "2330", "trade_date": "2026-07-07", "top_buy_lots": 6840, "top_sell_lots": 2570,
             "net_lots": 4270, "concentration_pct": 14.05}
        )
        rt.send("登入dada")
        rt.send("新增2330 1000 850")
        rt.send("我的股票")
        content = json.dumps(rt.last_message()["contents"], ensure_ascii=False)
        assert "主力 +4,270 張（集中 +14.1%）" in content
