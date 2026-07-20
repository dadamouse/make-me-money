"""整合測試：mock Supabase(PostgREST)、TWSE、LINE，驗證 webhook 端到端行為。"""
import base64
import hashlib
import hmac
import json
from urllib.parse import unquote

import httpx
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.webview import portfolio_sig, verify_portfolio_sig

SETTINGS = Settings(
    supabase_url="https://fake.supabase.co",
    supabase_service_role_key="service-key",
    line_channel_secret="channel-secret",
    line_channel_access_token="access-token",
    cron_secret="cron-secret",
    base_url="http://testserver",
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

LISTED_INSTITUTIONAL = {
    "stat": "OK",
    "date": "20260702",
    "fields": ["證券代號", "證券名稱", "外陸資買賣超股數(不含外資自營商)", "投信買賣超股數", "自營商買賣超股數", "三大法人買賣超股數"],
    "data": [["2330", "台積電", "5,000,000", "1,000,000", "-500,000", "5,500,000"]],
}

TPEX_INSTITUTIONAL = [
    {
        "Date": "1150702",
        "SecuritiesCompanyCode": "5274",
        "Foreign Investors include Mainland Area Investors (Foreign Dealers excluded)-Difference": "200,000",
        "SecuritiesInvestmentTrustCompanies-Difference": "-10,000",
        "Dealers-Difference": "5,000",
        "TotalDifference": "195,000",
    },
]

VOLUME_RANK = [
    {"stock_no": "3231", "trade_date": "2026-07-03", "close": 159.0, "prev_close": 158.5,
     "volume": 24768914.0, "prev_volume": 17411425.0},
    {"stock_no": "2330", "trade_date": "2026-07-03", "close": 2445.0, "prev_close": 2465.0,
     "volume": 35919290.0, "prev_volume": 32905868.0},
]

YAHOO_FIXTURES = {
    "^VIX": [16.5, 18.5],
    "^DJI": [52900.07, 53055.91],
    "^GSPC": [7483.24, 7537.43],
    "^IXIC": [25832.67, 26121.16],
    "^SOX": [13353.28, 12900.14],
    "^N225": [69737.69, 68256.96],
    "^KS11": [8051.33, 7656.31],
    "TSM": [434.16, 451.79],
    # 60 點序列：日 +0.31%、5日 +1.59%、月線上方 1.4%、收在近 3 月最高（貶值極端）
    "USDTWD=X": [31.5] * 57 + [31.6, 31.9, 32.0],
}

def _market_series_fixture():
    """desc 序列：最新 45479.11（-2.3%），前一日 46556.39，其餘 18 天 46000；量能比 1.05。"""
    rows = [
        {"trade_date": "2026-07-07", "taiex": 45479.11, "amount": 10.5e12},
        {"trade_date": "2026-07-06", "taiex": 46556.39, "amount": 10e12},
    ]
    for i in range(18):
        rows.append({"trade_date": f"2026-06-{20 - i:02d}", "taiex": 46000.0, "amount": 10e12})
    return rows


RPC_FIXTURES = {
    "market_series": _market_series_fixture(),
    "market_flow_series": [
        {"trade_date": "2026-07-06", "insti_net": -242258868, "margin_chg": -42283},
        {"trade_date": "2026-07-03", "insti_net": 120316000, "margin_chg": 102565},
        {"trade_date": "2026-07-02", "insti_net": None, "margin_chg": 115073},
    ],
    "market_breadth": [{"up_count": 1177, "down_count": 965, "new_high": 339, "new_low": 55}],
    "market_daily_summary": [
        {"insti_date": "2026-07-06", "institutional_net": -242258868, "margin_date": "2026-07-06", "margin_change": -42283},
    ],
    "volume_surge_ranking": VOLUME_RANK,
    "snapshot_depth": [{"insti_days": 3, "close_days": 25, "margin_days": 2}],
    "margin_reduce_price_up_picks": [
        {"stock_no": "2609", "stock_name": "陽明", "margin_change": -1520, "close": 71.5, "prev_close": 70.0},
    ],
    "short_margin_ratio_picks": [
        {"stock_no": "2353", "stock_name": "宏碁", "short_balance": 14221, "margin_balance": 42781, "ratio": 33.2},
    ],
    "institutional_streak_picks": [
        {"stock_no": "2330", "stock_name": "台積電", "foreign_streak": True, "trust_streak": False, "sum_net": 35000000},
    ],
    # dict 形式＝依 p_market 回不同結果
    "co_buy_picks": {
        "上市": [{"stock_no": "2834", "stock_name": "臺企銀", "foreign_net": 19913948, "trust_net": 8994}],
        "上櫃": [{"stock_no": "6182", "stock_name": "合晶", "foreign_net": 3798360, "trust_net": 5371000}],
    },
    "breakout_picks": [
        {"stock_no": "3231", "stock_name": "緯創", "close": 159, "high20": 150, "volume": 24768914, "avg_volume": 12000000},
    ],
    "kd_golden_cross_picks": [
        {"stock_no": "0050", "stock_name": "元大台灣50", "close": 108.8, "k_val": 25.3, "d_val": 22.1},
    ],
    "kd_pre_cross_picks": [
        {"stock_no": "2353", "stock_name": "宏碁", "close": 71.2, "k_val": 22.5, "d_val": 26.0,
         "trigger_price": 71.9, "gain_needed_pct": 0.98},
    ],
    "momentum_picks": [
        {"stock_no": "2466", "stock_name": "冠西電", "close": 94.8, "base_close": 59.0, "gain_pct": 60.7},
    ],
    "holders_depth": [{"weeks": 2}],
    "sector_momentum_rank": [{"industry": "半導體", "median_pct": 3.2, "rank": 2, "total": 34}],
    "correlated_peers": [
        {"stock_no": "2454", "stock_name": "聯發科", "correlation": 0.72, "pct": 6.2},
        {"stock_no": "3711", "stock_name": "日月光投控", "correlation": 0.66, "pct": 3.9},
    ],
    "concentration_picks": [
        {"stock_no": "8033", "stock_name": "雷虎", "big_ratio": 45.2, "ratio_change": 1.2, "holders_change_pct": -2.3},
    ],
}

TDCC_CSV = "\n".join(
    [
        "資料日期,證券代號,持股分級,人數,股數,占集保庫存數比例%",
        "20260703,2330  ,15,1481,22066084295,85.09",
        "20260703,2330  ,17,2898020,25932370067,100.00",
        "20260703,3231  ,15,890,1200000000,42.50",
        "20260703,3231  ,17,350000,2800000000,100.00",
    ]
)

TWSE_OK = {
    "stat": "OK",
    "data": [["115/07/02", "1,000", "2,355,000", "2,350.00", "2,360.00", "2,340.00", "2,355.00", "+5.00", "100"]],
}

GOOGLE_NEWS_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
""" + "\n".join(
    f"<item><title>新聞標題{i}</title><link>https://news.google.com/a{i}</link>"
    f"<pubDate>Mon, 20 Jul 2026 01:00:00 GMT</pubDate><source url='https://x'>來源{i}</source></item>"
    for i in range(1, 13)
) + """
</channel></rss>"""


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
            "daily_institutional": [],
            "dividend_events": [],
            "daily_market": [],
            "weekly_holders": [],
            "daily_broker_flows": [],
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
            elif value.startswith("ilike.") and value.endswith("*"):
                prefix = value[6:-1].lower()
                filters.append(lambda row, k=key, p=prefix: str(row.get(k, "")).lower().startswith(p))
            elif value.startswith("ilike."):
                exact = value[6:].lower()
                filters.append(lambda row, k=key, v=exact: str(row.get(k, "")).lower() == v)
            elif value.startswith("gte."):
                filters.append(lambda row, k=key, v=value[4:]: str(row.get(k, "")) >= v)
        match = lambda row: all(f(row) for f in filters)  # noqa: E731

        if method == "GET":
            results = [row for row in self.db[table] if match(row)]
            if params.get("order"):
                # 支援複合排序：stock_no,trade_date.desc
                sort_keys = []
                for part in params.get("order").split(","):
                    field, _, direction = part.partition(".")
                    sort_keys.append((field.strip(), direction.strip() == "desc"))
                for field, desc in reversed(sort_keys):
                    results = sorted(results, key=lambda row, f=field: str(row.get(f, "")), reverse=desc)
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


MIS_FIXTURES = {
    "t00": {"c": "t00", "n": "發行量加權股價指數", "z": "45479.11", "y": "46556.39", "t": "08:35:00"},
    "2330": {
        "c": "2330", "n": "台積電", "z": "2440.0000", "y": "2460.0000", "t": "08:35:00",
        "d": "20260708", "o": "2450.0000", "h": "2470.0000", "l": "2430.0000", "v": "25000",
    },
}


class BotRuntime:
    def __init__(self, twse_response=TWSE_OK, rpc_overrides=None, holiday_fixture=None, mis_fixtures=None):
        self.postgrest = FakePostgrest()
        self.replies = []
        self.rpc_fixtures = {**RPC_FIXTURES, **(rpc_overrides or {})}
        self.holiday_fixture = holiday_fixture or []
        self.mis_fixtures = MIS_FIXTURES if mis_fixtures is None else mis_fixtures

        def route(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if url.startswith("https://api.line.me/"):
                self.replies.append(json.loads(request.content))
                return httpx.Response(200, json={})
            if url.startswith("https://query1.finance.yahoo.com/v8/finance/chart/"):
                symbol = unquote(url.split("/chart/")[1].split("?")[0])
                closes = YAHOO_FIXTURES.get(symbol)
                if closes is None:
                    return httpx.Response(404, json={})
                return httpx.Response(
                    200,
                    json={"chart": {"result": [{"meta": {"symbol": symbol}, "indicators": {"quote": [{"close": closes}]}}]}},
                )
            if url.startswith("https://www.twse.com.tw/rwd/zh/afterTrading/FMTQIK"):
                return httpx.Response(
                    200,
                    json={
                        "stat": "OK",
                        "data": [
                            ["115/07/06", "8,111,222,333", "10,644,000,000,000", "2,222,333", "46,556.39", "-224.66"],
                            ["115/07/07", "9,111,222,333", "12,281,000,000,000", "2,555,333", "45,479.11", "-1,077.28"],
                        ],
                    },
                )
            if url.startswith("https://mis.twse.com.tw/stock/api/getStockInfo.jsp"):
                requested = request.url.params.get("ex_ch", "")
                msgs = []
                for channel in requested.split("|"):
                    code = channel.split("_")[1].split(".")[0] if "_" in channel else channel
                    if code in self.mis_fixtures:
                        msgs.append(self.mis_fixtures[code])
                return httpx.Response(200, json={"rtcode": "0000", "msgArray": msgs})
            if url.startswith("https://openapi.twse.com.tw/v1/holidaySchedule/holidaySchedule"):
                return httpx.Response(200, json=self.holiday_fixture)
            if url.startswith("https://news.google.com/rss/search"):
                return httpx.Response(200, text=GOOGLE_NEWS_RSS)
            if url.startswith("https://www.twse.com.tw/rwd/zh/fund/T86"):
                return httpx.Response(200, json=LISTED_INSTITUTIONAL)
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
            if url.startswith("https://opendata.tdcc.com.tw/"):
                return httpx.Response(200, text=TDCC_CSV)
            if url.startswith("https://fubon-ebrokerdj.fbs.com.tw/"):
                from test_broker_flows import ZCO_HTML

                return httpx.Response(200, content=ZCO_HTML.encode("cp950"))
            if url.startswith("https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O"):
                return httpx.Response(200, json=OTC_COMPANIES)
            if url.startswith("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes"):
                return httpx.Response(200, json=TPEX_QUOTES)
            if url.startswith("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_margin_balance"):
                return httpx.Response(200, json=TPEX_MARGINS)
            if url.startswith("https://www.tpex.org.tw/openapi/v1/tpex_exright_prepost"):
                return httpx.Response(200, json=TPEX_DIVIDENDS)
            if url.startswith("https://www.tpex.org.tw/openapi/v1/tpex_3insti_daily_trading"):
                return httpx.Response(200, json=TPEX_INSTITUTIONAL)
            if "/rest/v1/rpc/" in url:
                fn_name = url.split("/rest/v1/rpc/")[1].split("?")[0]
                fixture = self.rpc_fixtures.get(fn_name, [])
                if isinstance(fixture, dict):
                    args = json.loads(request.content) if request.content else {}
                    fixture = fixture.get(args.get("p_market"), [])
                return httpx.Response(200, json=fixture)
            if "/rest/v1/" in url:
                table = url.split("/rest/v1/")[1].split("?")[0]
                body = json.loads(request.content) if request.content else None
                return httpx.Response(200, json=self.postgrest.handle(request.method, table, request.url.params, body))
            raise AssertionError(f"unexpected url {url}")

        app = create_app(settings=SETTINGS, transport=httpx.MockTransport(route))
        self._client_ctx = TestClient(app)

    def __enter__(self):
        self.client = self._client_ctx.__enter__()
        # 對照表同步已改為背景執行，等它完成再開始測試（mock 環境毫秒級）
        import time as _time

        for _ in range(200):
            if self.postgrest.db["stocks"]:
                break
            _time.sleep(0.01)
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


def test_name_prefix_matches_starred_stock():
    """減資/分割後名稱帶星號（如「國巨*」），輸入不帶星號也要找得到。"""
    with BotRuntime() as rt:
        rt.postgrest.db["stocks"].append({"stock_no": "2327", "name": "國巨*", "industry": "電子零組件", "market": "上市"})
        rt.send("登入dada")
        rt.send("新增國巨")
        assert "已為 dada 新增 國巨*（2327・上市）" in rt.last_reply()
        assert rt.postgrest.db["holdings"][-1]["stock_no"] == "2327"


def test_ky_stock_name_variants_all_match():
    """KY 股輸入變體：「捷敏-ky」（小寫）、「捷敏」（省略後綴）、「捷敏ky」（少連字號）都要找到「捷敏-KY」。"""
    for user_input in ("捷敏-ky", "捷敏", "捷敏ky"):
        with BotRuntime() as rt:
            rt.postgrest.db["stocks"].append(
                {"stock_no": "6525", "name": "捷敏-KY", "industry": "半導體", "market": "上市"}
            )
            rt.send("登入dada")
            rt.send(f"新增{user_input}")
            assert "已為 dada 新增 捷敏-KY（6525・上市）" in rt.last_reply(), f"輸入「{user_input}」應找到捷敏-KY"


def test_name_prefix_multiple_matches_offers_pick():
    with BotRuntime() as rt:
        rt.postgrest.db["stocks"].append({"stock_no": "2327", "name": "國巨*", "industry": "電子零組件", "market": "上市"})
        rt.postgrest.db["stocks"].append({"stock_no": "9999", "name": "國巨測試", "industry": "其他", "market": "上櫃"})
        rt.send("登入dada")
        rt.send("新增國巨")
        reply = rt.last_reply()
        assert "有多個符合，請回覆數字選擇" in reply
        rt.send("1")
        assert "已為 dada 新增 國巨*（2327・上市）" in rt.last_reply()


def test_ambiguous_code_offers_numbered_pick():
    with BotRuntime() as rt:
        rt.send("登入dada")
        rt.send("新增0063 1000 35")
        reply = rt.last_reply()
        assert "有多個符合，請回覆數字選擇" in reply
        assert "1. 00631L 元大台灣50正2" in reply
        assert "2. 00632R 元大台灣50反1" in reply

        rt.send("9")  # 超出 1-6 範圍 → 不是 pick，回功能選單
        assert "功能選單" in rt.last_reply()

        rt.send("1")
        assert "已為 dada 新增 元大台灣50正2（00631L・上市）1,000 股＠35" in rt.last_reply()
        holdings = rt.postgrest.db["holdings"]
        assert holdings[-1]["stock_no"] == "00631L"
        assert holdings[-1]["shares"] == 1000

        rt.send("1")  # 待選已消耗 → 數字改當功能選單捷徑（1＝簡易持股）
        assert rt.last_message()["type"] == "flex"
        assert "dada 的持股" in rt.last_reply()


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
                "open": close - 1,
                "high": close + 2,
                "low": close - 2,
                "volume": 1000,
            }
        )


def test_list_shows_indicators_institutional_and_margin():
    with BotRuntime() as rt:
        _seed_history(rt, "2330")
        rt.postgrest.db["daily_margins"].append(
            {"stock_no": "2330", "trade_date": "2026-07-02", "margin_balance": 25000,
             "margin_change": 1000, "short_balance": 500, "short_change": -100}
        )
        rt.postgrest.db["daily_institutional"].append(
            {"stock_no": "2330", "trade_date": "2026-07-02", "foreign_net": 5000000,
             "trust_net": 1000000, "dealer_net": -500000, "total_net": 5500000}
        )
        rt.send("登入dada")
        rt.send("新增2330 1000 850")
        rt.send("我的股票")
        content = json.dumps(rt.last_message()["contents"], ensure_ascii=False)
        assert "MA5 " in content
        assert "MA20 " in content
        assert "MA60 " in content
        assert "RSI " in content
        assert "／J " in content
        assert "法人 +5,500 張" in content  # 上色 span
        assert "（外資+5,000｜投信+1,000｜自營-500）" in content
        assert "融資 25,000 張（+1,000）｜融券 500 張（-100）" in content
        assert '"text": "圖2330"' in content  # 點持股區塊可看該股線圖


def test_chart_command_returns_flex_with_served_image():
    with BotRuntime() as rt:
        _seed_history(rt, "2330")
        rt.send("登入dada")
        rt.send("線圖2330")
        message = rt.last_message()
        assert message["type"] == "flex"
        assert "2330 台積電 K線圖" in message["altText"]
        hero_url = message["contents"]["hero"]["url"]
        assert hero_url.startswith("http://testserver/charts/")
        image = rt.client.get(hero_url.replace("http://testserver", ""))
        assert image.status_code == 200
        assert image.content[:8] == b"\x89PNG\r\n\x1a\n"
        content = json.dumps(message["contents"], ensure_ascii=False)
        assert "MA20 " in content
        assert '"text": "2,440"' in content  # 點圖當下的 MIS 即時價成為最後一根 K 棒
        # 單檔網頁版連結與頁面
        assert "/s/2330?sig=" in content
        from app.webview import stock_sig

        page = rt.client.get(f"/s/2330?sig={stock_sig('2330', 'channel-secret')}")
        assert page.status_code == 200
        assert "2330 台積電" in page.text
        assert "/stock-chart/2330.png" in page.text
        assert rt.client.get("/s/2330?sig=bogus").status_code == 403


def test_chart_command_with_insufficient_history():
    with BotRuntime() as rt:
        rt.send("登入dada")
        rt.send("線圖3231")  # 只有 mock 的單日資料，回補也只拿到 1 筆
        assert "歷史資料不足" in rt.last_reply()


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


def test_volume_rank_command():
    with BotRuntime() as rt:
        rt.send("量增排行")  # 不需登入
        reply = rt.last_reply()
        assert "📈 量增排行（07/03）" in reply
        assert "1. 3231 緯創" in reply
        assert "24,768.91 張・前日 ×1.4｜收 159（+0.3%）" in reply
        assert "2. 2330 台積電" in reply
        assert "（-0.8%）" in reply


def test_daily_picks_flex_carousel():
    with BotRuntime() as rt:
        rt.send("每日選股")  # 不需登入
        message = rt.last_message()
        assert message["type"] == "flex"
        assert "🎯 每日選股" in message["altText"]
        carousel = message["contents"]
        assert carousel["type"] == "carousel"
        assert len(carousel["contents"]) == 8  # 八個策略各一張卡片（籌碼集中移至週六週報）
        content = json.dumps(carousel, ensure_ascii=False)
        assert "法人連買 3 日" in content
        assert "外資或投信連續 3 個交易日買超" in content  # 篩選邏輯說明
        assert "▍上市" in content
        assert "▍上櫃" in content
        # 上市/上櫃各自排名：同買策略兩市場結果不同
        assert "2834 臺企銀" in content
        assert "外資 +19,914 張、投信 +9 張" in content
        assert "6182 合晶" in content
        assert content.index("臺企銀") < content.index("合晶")
        assert "收 159 創20日新高，量為5日均量 2.1 倍" in content
        assert "K 25 上穿 D 22" in content
        # KD 蓄勢交叉（明日觀察）：反解出的觸發價與需要的漲幅
        assert "KD 蓄勢交叉（明日觀察）" in content
        assert "K 22／D 26，明收 ≥71.9（+1.0%）即黃金交叉" in content
        assert "融資 -1,520 張、股價 +2.1%" in content
        assert "券資比 33.2%" in content
        assert "5 日強勢股" in content
        assert "5 日漲 60.7%（59 → 94.8）" in content
        # 點個股 → 自動送出「圖XXXX」
        assert '"text": "圖2834"' in content
        # 網頁版按鈕
        assert "/picks?sig=" in content


def test_daily_picks_card_caps_at_five_web_shows_all():
    many = [
        {"stock_no": f"11{i:02d}", "stock_name": f"測試{i}", "foreign_net": 1000000 - i, "trust_net": 1000}
        for i in range(8)
    ]
    with BotRuntime(rpc_overrides={"co_buy_picks": {"上市": many, "上櫃": []}}) as rt:
        rt.send("選股")
        content = json.dumps(rt.last_message()["contents"], ensure_ascii=False)
        assert "1104 測試4" in content  # 第 5 檔有顯示
        assert "1105 測試5" not in content  # 第 6 檔起卡片不顯示
        assert "…還有 3 檔，開網頁版看完整前 10 名" in content

        from app.webview import picks_sig

        page = rt.client.get(f"/picks?sig={picks_sig('channel-secret')}")
        assert "1107 測試7" in page.text  # 網頁版完整列出


def test_daily_picks_skips_strategies_without_data():
    with BotRuntime(rpc_overrides={"snapshot_depth": [{"insti_days": 1, "close_days": 10}]}) as rt:
        rt.send("選股")
        content = json.dumps(rt.last_message()["contents"], ensure_ascii=False)
        assert "資料累積中（需 3 個交易日，目前 1）" in content  # 法人連買 skip
        assert "資料累積中（需 21 個交易日，目前 10）" in content  # 突破 skip
        assert "2834 臺企銀" in content  # 同買仍可跑


def test_daily_picks_push_endpoint():
    with BotRuntime() as rt:
        rt.send("登入dada")
        # 今日（conftest 固定為 2026-07-10）有新收盤資料 → 正常推播
        rt.postgrest.db["daily_closes"].append({"stock_no": "2330", "trade_date": "2026-07-10", "close": 2465.0})
        response = rt.client.post("/admin/daily-picks", headers={"x-cron-secret": "cron-secret"})
        assert response.status_code == 200
        payload = response.json()
        assert payload["ok"] is True
        assert payload["pushed"] == 1
        push = rt.replies[-1]
        assert push["to"] == ["U-test"]
        assert push["messages"][0]["type"] == "flex"  # 第一則：大盤體檢卡片（含走勢圖）
        assert "大盤體檢" in push["messages"][0]["altText"]
        assert push["messages"][1]["type"] == "flex"  # 第二則：選股卡片
        assert "🎯 每日選股" in push["messages"][1]["altText"]

        assert rt.client.post("/admin/daily-picks", headers={"x-cron-secret": "wrong"}).status_code == 403


def test_daily_picks_skipped_when_no_fresh_close():
    """休市日（颱風/假日）快照沒有今日資料 → 不重複推播前一日選股。"""
    with BotRuntime() as rt:
        rt.send("登入dada")
        rt.postgrest.db["daily_closes"].append({"stock_no": "2330", "trade_date": "2026-07-09", "close": 2465.0})
        pushes_before = len(rt.replies)
        response = rt.client.post("/admin/daily-picks", headers={"x-cron-secret": "cron-secret"})
        assert response.status_code == 200
        payload = response.json()
        assert payload["pushed"] == 0
        assert payload["skipped"] == "no fresh close data"
        assert len(rt.replies) == pushes_before  # 沒有任何 LINE 推播


def test_market_health_command():
    with BotRuntime() as rt:
        rt.send("體檢")  # 不需登入；「大盤」「6」也可
        message = rt.last_message()
        assert message["type"] == "flex"
        assert message["altText"].startswith("📋 大盤體檢（07/07）")
        bubble = message["contents"]
        assert bubble["hero"]["url"].startswith("http://testserver/charts/")  # 加權指數走勢圖
        content = json.dumps(bubble, ensure_ascii=False)
        assert "📋 大盤體檢（07/07）" in content
        assert "加權指數 45,479.11（-2.3%）" in content
        assert "位置：月線下方 1.1%" in content
        assert "量能：5 日均量的 1.05 倍" in content
        assert "大盤 RSI14：" in content
        assert "匯率：美元/台幣 32.00（今日 +0.31%｜5日 +1.59%）" in content
        assert "台幣趨勢：月線上方 1.4%（貶值趨勢）" in content
        # 法人資料日（07/06）非今天（conftest 固定 07/10）→ 標日期而非「今日」
        assert "法人：07/06 -242,259 張（連 1 日賣超）" in content
        assert "融資：-42,283 張｜5 日累計 +175,355 張（資料日 07/06）" in content
        assert "寬度：漲 1,177 家／跌 965 家｜創20日新高 339／新低 55" in content
        assert "VIX 恐慌指數：18.5（+12.1%）" in content
        # 白話解讀（描述現況、不做預測）
        assert "【📖 白話解讀】" in content
        assert "指數收在月線之下 → 短線趨勢偏弱" in content
        assert "台幣貶破近 3 個月低點 → 外資匯出訊號強" in content
        assert "法人連 1 日賣超 → 大戶偏保守" in content
        assert "融資 5 日大增 → 散戶槓桿升溫" in content
        assert "VIX 跳升 → 國際避險情緒升溫" in content
        assert "僅陳列現況供判讀" in content
        # 走勢圖可實際取得
        image_path = bubble["hero"]["url"].replace("http://testserver", "")
        image = rt.client.get(image_path)
        assert image.status_code == 200
        assert image.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_daily_picks_push_includes_health():
    with BotRuntime() as rt:
        rt.send("登入dada")
        rt.postgrest.db["daily_closes"].append({"stock_no": "2330", "trade_date": "2026-07-10", "close": 2465.0})
        rt.client.post("/admin/daily-picks", headers={"x-cron-secret": "cron-secret"})
        push = rt.replies[-1]
        assert len(push["messages"]) == 2
        assert push["messages"][0]["type"] == "flex"
        assert "大盤體檢" in push["messages"][0]["altText"]
        assert push["messages"][1]["type"] == "flex"


def test_picks_web_page():
    with BotRuntime() as rt:
        from app.webview import picks_sig

        page = rt.client.get(f"/picks?sig={picks_sig('channel-secret')}")
        assert page.status_code == 200
        assert "每日選股" in page.text
        assert "法人連買 3 日" in page.text
        assert "/stock-chart/2834.png" in page.text
        assert rt.client.get("/picks?sig=bogus").status_code == 403


def test_backfill_history_endpoint():
    with BotRuntime() as rt:
        assert rt.client.post("/admin/backfill-history", headers={"x-cron-secret": "wrong"}).status_code == 403
        response = rt.client.post("/admin/backfill-history", headers={"x-cron-secret": "cron-secret"})
        assert response.status_code == 200
        payload = response.json()
        assert payload["ok"] is True
        assert payload["total"] == 7  # 對照表中的全部股票
        # 背景任務在事件迴圈執行緒中進行；用 GET 輪詢進度（不會重新觸發）
        import time as _time

        status = payload
        for _ in range(100):
            _time.sleep(0.05)
            status = rt.client.get("/admin/backfill-history", headers={"x-cron-secret": "cron-secret"}).json()
            if not status.get("running"):
                break
        assert status["running"] is False
        assert status["done"] == status["total"] == 7


def test_batch_add_multiline():
    with BotRuntime() as rt:
        rt.send("登入dada")
        rt.send("新增 2330 40 2263.2\n新增 3231 3000 82.52\n新增 5274 100 3000\n我的股票")
        reply = rt.last_reply()
        assert "已為 dada 新增 台積電（2330・上市）40 股＠2,263.2" in reply
        assert "已為 dada 新增 緯創（3231・上市）3,000 股＠82.52" in reply
        assert "已為 dada 新增 信驊（5274・上櫃）100 股＠3,000" in reply
        assert "（卡片類指令請單獨輸入）" in reply  # 我的股票回卡片，批次中提示單獨輸入
        assert len(rt.postgrest.db["holdings"]) == 3


def test_clear_all_requires_confirmation():
    with BotRuntime() as rt:
        rt.send("登入dada")
        rt.send("新增2330 1000 850")
        rt.send("新增3231")
        rt.send("清空持股")
        assert "即將刪除「dada」的全部 2 筆持股紀錄，回覆「確認」執行" in rt.last_reply()
        assert len(rt.postgrest.db["holdings"]) == 2  # 尚未刪除

        rt.send("確認")
        assert "已清空「dada」的持股，共刪除 2 筆" in rt.last_reply()
        assert rt.postgrest.db["holdings"] == []

        rt.send("確認")  # 沒有待確認操作
        assert "目前沒有待確認的操作" in rt.last_reply()


def test_clear_without_holdings():
    with BotRuntime() as rt:
        rt.send("登入dada")
        rt.send("清空持股")
        assert "沒有任何持股紀錄" in rt.last_reply()


def test_requires_login_and_shows_help():
    with BotRuntime() as rt:
        rt.send("我的股票")
        assert "請先輸入「登入你的名字」" in rt.last_reply()
        rt.send("哈囉")
        assert "功能選單" in rt.last_reply()


def test_menu_number_shortcuts():
    with BotRuntime() as rt:
        rt.send("登入dada")
        rt.send("新增2330 1000 850")
        rt.send("1")  # 簡易持股
        assert rt.last_message()["type"] == "flex"
        assert "dada 的持股" in rt.last_reply()
        rt.send("4")  # 量增排行
        assert "📈 量增排行" in rt.last_reply()
        rt.send("3")  # 今日資訊
        assert "持股今日資訊" in rt.last_reply()


def test_detailed_holdings_carousel():
    with BotRuntime() as rt:
        _seed_history(rt, "2330")
        rt.send("登入dada")
        rt.send("新增2330 1000 850")
        rt.send("新增3231")  # 歷史不足，應被略過
        rt.send("詳細持股")
        message = rt.last_message()
        assert message["type"] == "flex"
        assert message["contents"]["type"] == "carousel"
        bubbles = message["contents"]["contents"]
        assert len(bubbles) == 1
        assert bubbles[0]["size"] == "mega"
        assert bubbles[0]["hero"]["url"].startswith("http://testserver/charts/")
        assert "資料不足略過：3231" in message["altText"]
        content = json.dumps(bubbles[0], ensure_ascii=False)
        assert "2330 台積電" in content
        assert "MA20 " in content


def test_simple_holdings_alias():
    with BotRuntime() as rt:
        rt.send("登入dada")
        rt.send("新增2330 1000 850")
        rt.send("簡易持股")
        assert rt.last_message()["type"] == "flex"
        assert "dada 的持股" in rt.last_reply()


def test_portfolio_sig_verify():
    sig = portfolio_sig(1, "channel-secret")
    assert verify_portfolio_sig(1, "channel-secret", sig)
    assert not verify_portfolio_sig(1, "channel-secret", "bogus")
    assert not verify_portfolio_sig(2, "channel-secret", sig)
    assert not verify_portfolio_sig(1, "channel-secret", None)


def test_portfolio_card_has_web_link():
    with BotRuntime() as rt:
        rt.send("登入dada")
        rt.send("新增2330 1000 850")
        rt.send("簡易持股")
        content = json.dumps(rt.last_message()["contents"], ensure_ascii=False)
        assert "開啟網頁版" in content
        member_id = rt.postgrest.db["members"][0]["id"]
        assert f"http://testserver/p/{member_id}?sig={portfolio_sig(member_id, 'channel-secret')}" in content


def test_portfolio_web_page():
    with BotRuntime() as rt:
        _seed_history(rt, "2330")
        rt.send("登入dada")
        rt.send("新增2330 1000 850")
        member_id = rt.postgrest.db["members"][0]["id"]
        good_sig = portfolio_sig(member_id, "channel-secret")

        page = rt.client.get(f"/p/{member_id}?sig={good_sig}")
        assert page.status_code == 200
        assert "dada 的持股" in page.text
        assert "2330 台積電" in page.text
        assert "/stock-chart/2330.png" in page.text

        assert rt.client.get(f"/p/{member_id}?sig=bogus").status_code == 403
        assert rt.client.get(f"/p/999?sig={portfolio_sig(999, 'channel-secret')}").status_code == 404


def test_stock_chart_image_endpoint():
    with BotRuntime() as rt:
        _seed_history(rt, "2330")
        image = rt.client.get("/stock-chart/2330.png")
        assert image.status_code == 200
        assert image.content[:8] == b"\x89PNG\r\n\x1a\n"
        assert rt.client.get("/stock-chart/bad!code.png").status_code in (404, 422)


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
        assert payload["institutional"] == 2
        assert payload["dividends"] == 2
        assert payload["market"] == 2  # FMTQIK 大盤兩個交易日
        assert {r["trade_date"]: r["taiex"] for r in rt.postgrest.db["daily_market"]} == {
            "2026-07-06": 46556.39,
            "2026-07-07": 45479.11,
        }

        institutional = {(r["stock_no"], r["trade_date"]): r for r in rt.postgrest.db["daily_institutional"]}
        assert institutional[("2330", "2026-07-02")]["total_net"] == 5500000.0
        assert institutional[("5274", "2026-07-02")]["foreign_net"] == 200000.0

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


def test_multiline_shorthand_add_and_remove():
    """小技巧宣稱的用法要真的可行：多行貼上，每行一筆 +代號 股數 成本；- 刪除。"""
    with BotRuntime() as rt:
        rt.send("登入dada")
        rt.send("+2330 1000 850\n+0050 2000 100")
        reply = rt.last_reply()
        assert "已為 dada 新增 台積電（2330・上市）1,000 股＠850" in reply
        assert "已為 dada 新增 元大台灣50（0050・上市）2,000 股＠100" in reply
        rt.send("-2330")
        assert "已刪除" in rt.last_reply()
        codes = {h["stock_no"] for h in rt.postgrest.db["holdings"]}
        assert codes == {"0050"}


def test_market_health_merges_realtime_index_when_snapshot_stale():
    """快照最新是 07/07，MIS 即時資料是今天（07/10）→ 體檢用即時指數並標「即時」。"""
    mis = {
        "t00": {"c": "t00", "n": "發行量加權股價指數", "z": "46100.00", "y": "45479.11",
                "t": "11:30:00", "d": "20260710"},
    }
    with BotRuntime(mis_fixtures=mis) as rt:
        rt.send("體檢")
        message = rt.last_message()
        assert "📋 大盤體檢（07/10 即時）" in message["altText"]
        # 即時 46,100 vs 快照最新 45,479.11 → +1.4%（今天上漲就該顯示上漲）
        assert "加權指數 46,100（+1.4%）" in message["altText"]
        assert "量能（前一交易日）" in message["altText"]


def test_market_health_keeps_snapshot_when_mis_stale():
    """MIS 資料日不是今天（休市日情境）→ 維持快照數字，不標即時。"""
    mis = {
        "t00": {"c": "t00", "n": "發行量加權股價指數", "z": "46100.00", "y": "45479.11",
                "t": "13:33:00", "d": "20260709"},
    }
    with BotRuntime(mis_fixtures=mis) as rt:
        rt.send("體檢")
        message = rt.last_message()
        assert "📋 大盤體檢（07/07）" in message["altText"]
        assert "加權指數 45,479.11（-2.3%）" in message["altText"]


def test_login_name_is_case_insensitive():
    """「登入rita」要對到既有的「Rita」，不再長出重複成員（曾因此漏收週末推播）。"""
    with BotRuntime() as rt:
        rt.send("登入Rita", line_user_id="U-rita")
        rt.send("登入rita", line_user_id="U-other")
        assert "已登入「Rita」" in rt.last_reply()
        names = [m["name"] for m in rt.postgrest.db["members"]]
        assert names.count("Rita") == 1 and "rita" not in names


def test_switch_name_is_case_insensitive():
    with BotRuntime() as rt:
        rt.send("登入Rita", line_user_id="U-rita")
        rt.send("登入dada", line_user_id="U-dada")
        rt.send("切換RITA", line_user_id="U-dada")
        assert "已切換為「Rita」" in rt.last_reply()
