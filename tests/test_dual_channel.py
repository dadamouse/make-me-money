"""雙 LINE channel：webhook2 綁定 channel=2、推播按 channel 路由、早盤限定成員。"""
from dataclasses import replace

from test_bot import SETTINGS, BotRuntime

SETTINGS2 = replace(
    SETTINGS,
    line2_channel_secret="channel-secret-2",
    line2_channel_access_token="access-token-2",
)


def _multicasts(rt: BotRuntime) -> list[dict]:
    return [c for c in rt.line_calls if c["url"].endswith("/multicast")]


def test_login_via_webhook2_binds_channel_2():
    with BotRuntime(settings=SETTINGS2) as rt:
        response = rt.send("登入dada", line_user_id="U-dada", channel=2)
        assert response.status_code == 200
        assert "已登入「dada」" in rt.last_reply()
        binding = rt.postgrest.db["line_bindings"][0]
        assert binding["channel"] == 2
        assert rt.line_calls[-1]["auth"] == "Bearer access-token-2"  # 回覆用 channel 2 的 token


def test_login_via_webhook1_binds_channel_1():
    with BotRuntime(settings=SETTINGS2) as rt:
        rt.send("登入Gino", line_user_id="U-gino", channel=1)
        assert rt.postgrest.db["line_bindings"][0]["channel"] == 1
        assert rt.line_calls[-1]["auth"] == "Bearer access-token"


def test_relogin_via_webhook2_moves_binding_to_channel_2():
    """同一 LINE 帳號改到新官方帳號重新登入 → 綁定搬到 channel 2（搬家動線）。"""
    with BotRuntime(settings=SETTINGS2) as rt:
        rt.send("登入dada", line_user_id="U-dada", channel=1)
        rt.send("登入dada", line_user_id="U-dada", channel=2)
        assert len(rt.postgrest.db["line_bindings"]) == 1
        assert rt.postgrest.db["line_bindings"][0]["channel"] == 2


def test_webhook2_rejects_bad_signature():
    with BotRuntime(settings=SETTINGS2) as rt:
        assert rt.send("登入dada", channel=2, bad_signature=True).status_code == 403
        assert rt.replies == []


def test_webhook2_unconfigured_returns_404():
    with BotRuntime() as rt:  # 未設定 channel 2 → 端點不存在
        assert rt.send("登入dada", channel=2).status_code == 404


def test_daily_picks_multicast_routes_by_channel():
    with BotRuntime(settings=SETTINGS2) as rt:
        rt.send("登入Gino", line_user_id="U-gino", channel=1)
        rt.send("登入dada", line_user_id="U-dada", channel=2)
        rt.postgrest.db["daily_closes"].append({"stock_no": "2330", "trade_date": "2026-07-10", "close": 2465.0})
        response = rt.client.post("/admin/daily-picks", headers={"x-cron-secret": "cron-secret"})
        assert response.status_code == 200
        assert response.json()["pushed"] == 2
        by_auth = {c["auth"]: c["body"]["to"] for c in _multicasts(rt)}
        assert by_auth == {"Bearer access-token": ["U-gino"], "Bearer access-token-2": ["U-dada"]}


def test_legacy_binding_without_channel_defaults_to_channel_1():
    with BotRuntime(settings=SETTINGS2) as rt:
        rt.postgrest.db["members"].append({"id": 99, "name": "legacy"})
        rt.postgrest.db["line_bindings"].append(
            {"line_user_id": "U-legacy", "member_id": 99, "acting_member_id": 99}
        )
        rt.postgrest.db["daily_closes"].append({"stock_no": "2330", "trade_date": "2026-07-10", "close": 2465.0})
        rt.client.post("/admin/daily-picks", headers={"x-cron-secret": "cron-secret"})
        multicasts = _multicasts(rt)
        assert len(multicasts) == 1
        assert multicasts[0]["body"]["to"] == ["U-legacy"]
        assert multicasts[0]["auth"] == "Bearer access-token"


def test_morning_open_only_pushes_allowlist_members():
    """早盤導航只推 dada/Rita/cindy，其他成員（Gino）不收。"""
    with BotRuntime(settings=SETTINGS2) as rt:
        rt.send("登入dada", line_user_id="U-dada", channel=2)
        rt.send("登入Gino", line_user_id="U-gino", channel=1)
        rt.postgrest.db["daily_closes"] += [
            {"stock_no": "2330", "trade_date": "2026-07-06", "close": 2445.0},
            {"stock_no": "0050", "trade_date": "2026-07-06", "close": 108.25},
        ]
        response = rt.client.post("/admin/morning-open", headers={"x-cron-secret": "cron-secret"})
        assert response.status_code == 200
        assert response.json()["pushed"] == 1
        multicasts = _multicasts(rt)
        assert len(multicasts) == 1
        assert multicasts[0]["body"]["to"] == ["U-dada"]
        assert multicasts[0]["auth"] == "Bearer access-token-2"


def test_morning_open_allowlist_is_case_insensitive_on_login_casing():
    """成員名稱大小寫以資料庫為準：登入rita 對到既有 Rita 後仍收得到早盤。"""
    with BotRuntime(settings=SETTINGS2) as rt:
        rt.postgrest.db["members"].append({"id": 98, "name": "Rita"})
        rt.send("登入rita", line_user_id="U-rita", channel=2)
        rt.postgrest.db["daily_closes"] += [
            {"stock_no": "2330", "trade_date": "2026-07-06", "close": 2445.0},
            {"stock_no": "0050", "trade_date": "2026-07-06", "close": 108.25},
        ]
        response = rt.client.post("/admin/morning-open", headers={"x-cron-secret": "cron-secret"})
        assert response.json()["pushed"] == 1
        assert _multicasts(rt)[0]["body"]["to"] == ["U-rita"]


def test_follow_event_replies_help_menu():
    """加好友（follow 事件）自動回覆功能選單。"""
    with BotRuntime(settings=SETTINGS2) as rt:
        response = rt.follow(line_user_id="U-new", channel=1)
        assert response.status_code == 200
        assert response.json()["handled"] == 1
        assert "功能選單" in rt.last_reply()
        assert rt.line_calls[-1]["auth"] == "Bearer access-token"


def test_follow_event_on_channel_2_replies_via_channel_2_token():
    with BotRuntime(settings=SETTINGS2) as rt:
        response = rt.follow(line_user_id="U-new2", channel=2)
        assert response.status_code == 200
        assert "功能選單" in rt.last_reply()
        assert rt.line_calls[-1]["auth"] == "Bearer access-token-2"


def test_login_on_new_channel_removes_stale_binding_on_other_channel():
    """不同 Provider 下同一人 userId 不同：搬家登入後要清掉舊 channel 的綁定，避免重複推播。"""
    with BotRuntime(settings=SETTINGS2) as rt:
        rt.send("登入dada", line_user_id="U-old", channel=1)
        rt.send("登入dada", line_user_id="U-new", channel=2)
        bindings = rt.postgrest.db["line_bindings"]
        assert len(bindings) == 1
        assert bindings[0]["line_user_id"] == "U-new"
        assert bindings[0]["channel"] == 2


def test_admin_bot_info_reports_each_channel():
    with BotRuntime(settings=SETTINGS2) as rt:
        response = rt.client.get("/admin/bot-info", headers={"x-cron-secret": "cron-secret"})
        assert response.status_code == 200
        channels = response.json()["channels"]
        assert set(channels) == {"1", "2"}
        assert channels["1"]["basicId"] == "@fake-bot"
        auths = [c["auth"] for c in rt.line_calls if c["url"].endswith("/v2/bot/info")]
        assert auths == ["Bearer access-token", "Bearer access-token-2"]


def test_admin_bot_info_rejects_bad_secret():
    with BotRuntime() as rt:
        assert rt.client.get("/admin/bot-info", headers={"x-cron-secret": "wrong"}).status_code == 403
