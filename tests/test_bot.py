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
)

LISTED_COMPANIES = [
    {"公司代號": "2330", "公司簡稱": "台積電"},
    {"公司代號": "3231", "公司簡稱": "緯創"},
]

TWSE_OK = {
    "stat": "OK",
    "data": [["115/07/02", "1,000", "2,355,000", "2,350.00", "2,360.00", "2,340.00", "2,355.00", "+5.00", "100"]],
}


class FakePostgrest:
    """記憶體版 PostgREST：支援本專案用到的 eq / in / on_conflict 語法。"""

    def __init__(self):
        self.db = {"members": [], "line_bindings": [], "holdings": [], "stocks": []}
        self._next_id = 1

    def handle(self, method: str, table: str, params: httpx.QueryParams, body):
        filters = []
        for key, value in params.multi_items():
            if key in ("select", "on_conflict"):
                continue
            if value.startswith("eq."):
                filters.append(lambda row, k=key, v=value[3:]: str(row.get(k)) == v)
            elif value.startswith("in.(") and value.endswith(")"):
                allowed = value[4:-1].split(",")
                filters.append(lambda row, k=key, a=allowed: str(row.get(k)) in a)
        match = lambda row: all(f(row) for f in filters)  # noqa: E731

        if method == "GET":
            return [row for row in self.db[table] if match(row)]
        if method == "POST":
            conflict_key = params.get("on_conflict")
            results = []
            for item in body if isinstance(body, list) else [body]:
                existing = None
                if conflict_key:
                    existing = next((r for r in self.db[table] if r.get(conflict_key) == item.get(conflict_key)), None)
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
            if url.startswith("https://openapi.twse.com.tw/"):
                return httpx.Response(200, json=LISTED_COMPANIES)
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


def test_startup_syncs_stock_list():
    with BotRuntime() as rt:
        assert {s["stock_no"] for s in rt.postgrest.db["stocks"]} == {"2330", "3231"}


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
        assert "已為 dada 新增 台積電（2330）1,000 股＠850" in rt.last_reply()

        rt.send("新增緯創")
        assert "已為 dada 新增 緯創（3231）（觀察，未記股數）" in rt.last_reply()

        rt.send("我的股票")
        assert rt.last_message()["type"] == "flex"
        assert "2330 台積電" in json.dumps(rt.last_message()["contents"], ensure_ascii=False)
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
        assert "已刪除 dada 的 台積電（2330），共 1 筆" in rt.last_reply()
        dada_id = next(m["id"] for m in rt.postgrest.db["members"] if m["name"] == "dada")
        assert [h for h in rt.postgrest.db["holdings"] if h["member_id"] == dada_id and h["stock_no"] == "2330"] == []


def test_requires_login_and_shows_help():
    with BotRuntime() as rt:
        rt.send("我的股票")
        assert "請先輸入「登入你的名字」" in rt.last_reply()
        rt.send("哈囉")
        assert "指令說明" in rt.last_reply()


def test_missing_quote_shows_warning():
    with BotRuntime(twse_response={"stat": "很抱歉，沒有符合條件的資料!", "total": 0}) as rt:
        rt.send("登入dada")
        rt.send("新增2330")
        rt.send("我的股票")
        assert "⚠️ 查無報價" in rt.last_reply()
