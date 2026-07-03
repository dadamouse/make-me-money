"""整合測試：mock Supabase(PostgREST)、TWSE、LINE，驗證 webhook 端到端行為。"""
import base64
import hashlib
import hmac
import json

import httpx
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

SETTINGS = Settings(
    supabase_url="https://fake.supabase.co",
    supabase_service_role_key="service-key",
    line_channel_secret="channel-secret",
    line_channel_access_token="access-token",
    cron_secret="cron-secret",
)

LISTED_COMPANIES = [
    {"公司代號": "2330", "公司簡稱": "台積電", "產業別": "24"},
    {"公司代號": "3231", "公司簡稱": "緯創", "產業別": "25"},
]

OTC_COMPANIES = [
    {"Date": "1150703", "SecuritiesCompanyCode": "5274", "CompanyAbbreviation": "信驊", "SecuritiesIndustryCode": "24"},
]

TPEX_QUOTES = [
    {"Date": "1150702", "SecuritiesCompanyCode": "5274", "Close": "5000.00"},
    {"Date": "1150702", "SecuritiesCompanyCode": "006201", "CompanyName": "元大富櫃50", "Close": "49.30"},
]

LISTED_QUOTES_ALL = [
    {"Code": "0050", "Name": "元大台灣50", "Date": "1150702", "ClosingPrice": "108.80", "TradeVolume": "50,000"},
    {"Code": "00631L", "Name": "元大台灣50正2", "Date": "1150702", "ClosingPrice": "38.88", "TradeVolume": "10,000"},
    {"Code": "00632R", "Name": "元大台灣50反1", "Date": "1150702", "ClosingPrice": "3.55", "TradeVolume": "20,000"},
    {"Code": "2330", "Name": "台積電", "Date": "1150702", "ClosingPrice": "2465.00", "TradeVolume": "31,058,614"},
]

LISTED_MARGINS = [
    {"股票代號": "2330", "融資今日餘額": "25,000", "融資前日餘額": "24,000", "融券今日餘額": "500", "融券前日餘額": "600"},
]

LISTED_DIVIDENDS = [
    {"Date": "1150709", "Code": "2330", "Name": "台積電", "Exdividend": "息", "CashDividend": "5.00", "StockDividendRatio": ""},
]

TPEX_MARGINS = [
    {
        "Date": "1150703",
        "SecuritiesCompanyCode": "5274",
        "MarginPurchaseBalance": "1200",
        "MarginPurchaseBalancePreviousDay": "1000",
        "ShortSaleBalance": "10",
        "ShortSaleBalancePreviousDay": "15",
    },
]

TPEX_DIVIDENDS = [
    {
        "ExRrightsExDividendDate": "1150710",
        "SecuritiesCompanyCode": "5274",
        "ExRrightsExDividend": "除息",
        "CashDividend": "3.50000000",
        "StockDividendRatio": "0.00000000",
    },
]

LISTED_NEWS = [
    {"公司代號": "2330", "公司名稱": "台積電", "主旨 ": "公告本公司董事會決議發放現金股利"},
    {"公司代號": "9999", "公司名稱": "無關公司", "主旨 ": "不該出現的訊息"},
]

TPEX_NEWS = [
    {"SecuritiesCompanyCode": "5274", "CompanyName": "信驊", "主旨": "公告本公司取得美國專利"},
]

TWSE_OK = {
    "stat": "OK",
    "data": [["115/07/02", "1,000", "2,355,000", "2,350.00", "2,360.00", "2,340.00", "2,355.00", "+5.00", "100"]],
}


class FakePostgrest:
    """記憶體版 PostgREST：支援本專案用到的 eq / in / on_conflict 語法。"""

    def __init__(self):
        self.db = {
            "members": [],
            "line_bindings": [],
            "holdings": [],
            "stocks": [],
            "daily_closes": [],
            "daily_margins": [],
            "dividend_events": [],
        }
        self._next_id = 1

    def handle(self, method: str, table: str, params: httpx.QueryParams, body):
        filters = []
        for key, value in params.multi_items():
            if key in ("select", "on_conflict", "order", "limit"):
                continue
            if value.startswith("eq."):
                filters.append(lambda row, k=key, v=value[3:]: str(row.get(k)) == v)
            elif value.startswith("in.(") and value.endswith(")"):
                allowed = value[4:-1].split(",")
                filters.append(lambda row, k=key, a=allowed: str(row.get(k)) in a)
            elif value.startswith("like.") and value.endswith("*"):
                prefix = value[5:-1]
                filters.append(lambda row, k=key, p=prefix: str(row.get(k, "")).startswith(p))
            elif value.startswith("gte."):
                filters.append(lambda row, k=key, v=value[4:]: str(row.get(k, "")) >= v)
        match = lambda row: all(f(row) for f in filters)  # noqa: E731

        if method == "GET":
            results = [row for row in self.db[table] if match(row)]
            if params.get("order"):
                field, _, direction = params.get("order").partition(".")
                results = sorted(results, key=lambda row: str(row.get(field, "")), reverse=direction == "desc")
            if params.get("limit"):
                results = results[: int(params.get("limit"))]
            return results
        if method == "POST":
            conflict_keys = (params.get("on_conflict") or "").split(",") if params.get("on_conflict") else []
            results = []
            for item in body if isinstance(body, list) else [body]:
                existing = None
                if conflict_keys:
                    existing = next(
                        (r for r in self.db[table] if all(r.get(k) == item.get(k) for k in conflict_keys)), None
                    )
                if existing:
                    existing.update(item)
                    results.append(existing)
                    continue
                row = dict(item)
                if table in ("members", "holdings"):
                    row["id"] = self._next_id
                    self._next_id += 1
                self.db[table].append(row)
                results.append(row)
            return results
        if method == "PATCH":
            rows = [row for row in self.db[table] if match(row)]
            for row in rows:
                row.update(body)
            return rows
        if method == "DELETE":
            rows = [row for row in self.db[table] if match(row)]
            self.db[table] = [row for row in self.db[table] if not match(row)]
            return rows
        raise AssertionError(f"unexpected method {method}")


class BotRuntime:
    def __init__(self, twse_response=TWSE_OK):
        self.postgrest = FakePostgrest()
        self.replies = []

        def route(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if url.startswith("https://api.line.me/"):
                self.replies.append(json.loads(request.content))
                return httpx.Response(200, json={})
            if url.startswith("https://www.twse.com.tw/"):
                return httpx.Response(200, json=twse_response)
            if url.startswith("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"):
                return httpx.Response(200, json=LISTED_QUOTES_ALL)
            if url.startswith("https://openapi.twse.com.tw/v1/exchangeReport/MI_MARGN"):
                return httpx.Response(200, json=LISTED_MARGINS)
            if url.startswith("https://openapi.twse.com.tw/v1/exchangeReport/TWT48U_ALL"):
                return httpx.Response(200, json=LISTED_DIVIDENDS)
            if url.startswith("https://openapi.twse.com.tw/v1/opendata/t187ap04_L"):
                return httpx.Response(200, json=LISTED_NEWS)
            if url.startswith("https://openapi.twse.com.tw/"):
                return httpx.Response(200, json=LISTED_COMPANIES)
            if url.startswith("https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap04_O"):
                return httpx.Response(200, json=TPEX_NEWS)
            if url.startswith("https://www.tpex.org.tw/www/zh-tw/afterTrading/tradingStock"):
                return httpx.Response(200, json={"tables": []})
            if url.startswith("https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O"):
                return httpx.Response(200, json=OTC_COMPANIES)
            if url.startswith("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes"):
                return httpx.Response(200, json=TPEX_QUOTES)
            if url.startswith("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_margin_balance"):
                return httpx.Response(200, json=TPEX_MARGINS)
            if url.startswith("https://www.tpex.org.tw/openapi/v1/tpex_exright_prepost"):
                return httpx.Response(200, json=TPEX_DIVIDENDS)
            if "/rest/v1/" in url:
                table = url.split("/rest/v1/")[1].split("?")[0]
                body = json.loads(request.content) if request.content else None
                return httpx.Response(200, json=self.postgrest.handle(request.method, table, request.url.params, body))
            raise AssertionError(f"unexpected url {url}")

        app = create_app(settings=SETTINGS, transport=httpx.MockTransport(route))
        self._client_ctx = TestClient(app)

    def __enter__(self):
        self.client = self._client_ctx.__enter__()
        return self

    def __exit__(self, *args):
        return self._client_ctx.__exit__(*args)

    def send(self, text: str, line_user_id: str = "U-test", bad_signature: bool = False) -> httpx.Response:
        payload = {
            "events": [
                {
                    "type": "message",
                    "replyToken": "reply-token",
                    "source": {"userId": line_user_id},
                    "message": {"type": "text", "text": text},
                }
            ]
        }
        raw = json.dumps(payload).encode()
        signature = (
            "bogus"
            if bad_signature
            else base64.b64encode(hmac.new(SETTINGS.line_channel_secret.encode(), raw, hashlib.sha256).digest()).decode()
        )
        return self.client.post(
            "/webhook/line",
            content=raw,
            headers={"x-line-signature": signature, "content-type": "application/json"},
        )

    def last_message(self) -> dict | None:
        return self.replies[-1]["messages"][0] if self.replies else None

    def last_reply(self) -> str | None:
        """text 訊息回內文；flex 訊息回 altText（沿用純文字版格式）。"""
        message = self.last_message()
        if not message:
            return None
        return message.get("text") or message.get("altText")


def test_startup_syncs_listed_otc_and_etf_stocks():
    with BotRuntime() as rt:
        stocks = {s["stock_no"]: s for s in rt.postgrest.db["stocks"]}
        assert set(stocks) == {"2330", "3231", "5274", "0050", "006201", "00631L", "00632R"}
        assert stocks["2330"]["industry"] == "半導體"  # 公司資料優先，不被 STOCK_DAY_ALL 覆蓋成 ETF
        assert stocks["2330"]["market"] == "上市"
        assert stocks["5274"]["market"] == "上櫃"
        assert stocks["0050"] == {"stock_no": "0050", "name": "元大台灣50", "industry": "ETF", "market": "上市"}
        assert stocks["006201"] == {"stock_no": "006201", "name": "元大富櫃50", "industry": "ETF", "market": "上櫃"}


def test_invalid_signature_is_rejected_without_reply():
    with BotRuntime() as rt:
        response = rt.send("登入dada", bad_signature=True)
        assert response.status_code == 403
        assert rt.replies == []


def test_full_flow_login_add_list_switch_remove():
    with BotRuntime() as rt:
        rt.send("登入dada")
        assert "已登入「dada」" in rt.last_reply()
        assert rt.postgrest.db["members"][0]["name"] == "dada"
        assert rt.postgrest.db["line_bindings"][0]["member_id"] == rt.postgrest.db["members"][0]["id"]

        rt.send("新增2330 1000 850")
        assert "已為 dada 新增 台積電（2330・上市）1,000 股＠850" in rt.last_reply()

        rt.send("新增緯創")
        assert "已為 dada 新增 緯創（3231・上市）（觀察，未記股數）" in rt.last_reply()

        rt.send("我的股票")
        assert rt.last_message()["type"] == "flex"
        flex_content = json.dumps(rt.last_message()["contents"], ensure_ascii=False)
        assert "2330 台積電" in flex_content
        assert "半導體" in flex_content
        assert "電腦及週邊設備" in flex_content
        listing = rt.last_reply()
        assert "📊 dada 的持股" in listing
        assert "2330 台積電　收盤 2,355（07/02）" in listing
        assert "1,000 股｜市值 2,355,000｜損益 +1,505,000（+177.1%）" in listing
        assert "3231 緯創" in listing
        assert "觀察中（未記股數）" in listing

        rt.send("登入媽媽", line_user_id="U-mom")
        rt.send("切換媽媽")
        assert "已切換為「媽媽」" in rt.last_reply()
        rt.send("新增2330 500 900")
        assert "已為 媽媽 新增" in rt.last_reply()

        rt.send("切換dada")
        rt.send("刪除2330")
        assert "已刪除 dada 的 台積電（2330・上市），共 1 筆" in rt.last_reply()
        dada_id = next(m["id"] for m in rt.postgrest.db["members"] if m["name"] == "dada")
        assert [h for h in rt.postgrest.db["holdings"] if h["member_id"] == dada_id and h["stock_no"] == "2330"] == []


def test_otc_stock_uses_tpex_quote():
    with BotRuntime() as rt:
        rt.send("登入dada")
        rt.send("新增信驊 100 3000")
        assert "已為 dada 新增 信驊（5274・上櫃）100 股＠3,000" in rt.last_reply()
        rt.send("我的股票")
        content = json.dumps(rt.last_message()["contents"], ensure_ascii=False)
        assert "5274 信驊" in content
        assert "5,000" in content
        assert "上櫃｜100 股" in content
        assert "07/02" in content


def test_etf_by_name_grouped_and_labeled():
    with BotRuntime() as rt:
        rt.send("登入dada")
        rt.send("新增元大台灣50 1000 100")
        assert "已為 dada 新增 元大台灣50（0050・上市）1,000 股＠100" in rt.last_reply()
        rt.send("我的股票")
        content = json.dumps(rt.last_message()["contents"], ensure_ascii=False)
        assert "ETF" in content
        assert "0050 元大台灣50" in content
        assert "上市｜1,000 股" in content


def test_code_suffix_autocompleted_when_unique():
    with BotRuntime() as rt:
        rt.send("登入dada")
        rt.send("新增00631")
        assert "已為 dada 新增 元大台灣50正2（00631L・上市）" in rt.last_reply()


def test_ambiguous_code_offers_numbered_pick():
    with BotRuntime() as rt:
        rt.send("登入dada")
        rt.send("新增0063 1000 35")
        reply = rt.last_reply()
        assert "有多個符合，請回覆數字選擇" in reply
        assert "1. 00631L 元大台灣50正2" in reply
        assert "2. 00632R 元大台灣50反1" in reply

        rt.send("9")  # 超出 1-6 範圍 → 不是 pick，回指令說明
        assert "指令說明" in rt.last_reply()

        rt.send("1")
        assert "已為 dada 新增 元大台灣50正2（00631L・上市）1,000 股＠35" in rt.last_reply()
        holdings = rt.postgrest.db["holdings"]
        assert holdings[-1]["stock_no"] == "00631L"
        assert holdings[-1]["shares"] == 1000

        rt.send("1")  # 已消耗，再選一次應提示沒有待選項目
        assert "沒有等待選擇的項目" in rt.last_reply()


def test_pick_out_of_range_keeps_pending():
    with BotRuntime() as rt:
        rt.send("登入dada")
        rt.send("新增0063")
        rt.send("5")
        assert "請輸入 1～2 之間的數字" in rt.last_reply()
        rt.send("2")
        assert "已為 dada 新增 元大台灣50反1（00632R・上市）" in rt.last_reply()


def _seed_history(rt, stock_no, days=70):
    for i in range(days):
        close = 100.0 + i
        rt.postgrest.db["daily_closes"].append(
            {
                "stock_no": stock_no,
                "trade_date": f"2026-{4 + i // 30:02d}-{i % 30 + 1:02d}",
                "close": close,
                "high": close + 2,
                "low": close - 2,
                "volume": 1000,
            }
        )


def test_list_shows_technical_indicators_when_history_exists():
    with BotRuntime() as rt:
        _seed_history(rt, "2330")
        rt.send("登入dada")
        rt.send("新增2330 1000 850")
        rt.send("我的股票")
        content = json.dumps(rt.last_message()["contents"], ensure_ascii=False)
        assert "MA5 " in content
        assert "MA20 " in content
        assert "MA60 " in content
        assert "RSI " in content
        assert "K " in content


def test_news_command_filters_by_holdings():
    with BotRuntime() as rt:
        rt.send("登入dada")
        rt.send("新增2330")
        rt.send("新增信驊")
        rt.postgrest.db["dividend_events"].append(
            {"stock_no": "2330", "ex_date": "2099-01-01", "kind": "息", "cash_dividend": 5.0}
        )
        rt.send("今日資訊")
        reply = rt.last_reply()
        assert "持股今日資訊" in reply
        assert "2330 台積電：公告本公司董事會決議發放現金股利" in reply
        assert "5274 信驊：公告本公司取得美國專利" in reply
        assert "不該出現的訊息" not in reply
        assert "2099-01-01 除息，現金股利 5 元" in reply


def test_news_command_without_holdings():
    with BotRuntime() as rt:
        rt.send("登入dada")
        rt.send("今日資訊")
        assert "目前沒有任何持股" in rt.last_reply()


def test_requires_login_and_shows_help():
    with BotRuntime() as rt:
        rt.send("我的股票")
        assert "請先輸入「登入你的名字」" in rt.last_reply()
        rt.send("哈囉")
        assert "指令說明" in rt.last_reply()


def test_daily_snapshot_requires_secret():
    with BotRuntime() as rt:
        response = rt.client.post("/admin/daily-snapshot", headers={"x-cron-secret": "wrong"})
        assert response.status_code == 403
        assert rt.postgrest.db["daily_closes"] == []


def test_daily_snapshot_stores_closes_margins_dividends():
    with BotRuntime() as rt:
        response = rt.client.post("/admin/daily-snapshot", headers={"x-cron-secret": "cron-secret"})
        assert response.status_code == 200
        payload = response.json()
        assert payload["ok"] is True
        assert payload["stocks_synced"] == 7
        assert payload["closes"] == 6
        assert payload["margins"] == 2
        assert payload["dividends"] == 2

        closes = {(r["stock_no"], r["trade_date"]): r for r in rt.postgrest.db["daily_closes"]}
        assert closes[("2330", "2026-07-02")]["close"] == 2465.0
        assert closes[("2330", "2026-07-02")]["volume"] == 31058614.0
        assert closes[("5274", "2026-07-02")]["close"] == 5000.0

        margins = {(r["stock_no"], r["trade_date"]): r for r in rt.postgrest.db["daily_margins"]}
        assert margins[("2330", "2026-07-02")]["margin_change"] == 1000.0
        assert margins[("5274", "2026-07-03")]["short_change"] == -5.0

        dividends = {(r["stock_no"], r["ex_date"]): r for r in rt.postgrest.db["dividend_events"]}
        assert dividends[("2330", "2026-07-09")]["cash_dividend"] == 5.0
        assert dividends[("5274", "2026-07-10")]["kind"] == "息"

        # 重跑：全部 upsert，筆數不變
        rt.client.post("/admin/daily-snapshot", headers={"x-cron-secret": "cron-secret"})
        assert len(rt.postgrest.db["daily_closes"]) == 6
        assert len(rt.postgrest.db["daily_margins"]) == 2
        assert len(rt.postgrest.db["dividend_events"]) == 2


def test_missing_quote_shows_warning():
    with BotRuntime(twse_response={"stat": "很抱歉，沒有符合條件的資料!", "total": 0}) as rt:
        rt.send("登入dada")
        rt.send("新增2330")
        rt.send("我的股票")
        assert "⚠️ 查無報價" in rt.last_reply()
