from app.parser import parse_command
from app.pending import PendingChoices


def test_parse_chart_aliases():
    for text in ("圖2330", "線圖2330", "K線2330", "k線 2330", "圖 緯創"):
        cmd = parse_command(text)
        assert cmd.action == "chart", text
    assert parse_command("圖2330").stock == "2330"
    assert parse_command("圖 緯創").stock == "緯創"


def test_parse_pick_numbers():
    assert parse_command("1").action == "pick"
    assert parse_command("1").index == 1
    assert parse_command("6").index == 6
    assert parse_command("7").action == "help"
    assert parse_command("12").action == "help"


def test_pending_pop_within_ttl():
    clock = {"now": 0.0}
    pending = PendingChoices(ttl_seconds=300, clock=lambda: clock["now"])
    pending.put("U1", {"action": "add"})
    clock["now"] = 299
    assert pending.pop("U1") == {"action": "add"}
    assert pending.pop("U1") is None  # 已消耗


def test_pending_expires_after_ttl():
    clock = {"now": 0.0}
    pending = PendingChoices(ttl_seconds=300, clock=lambda: clock["now"])
    pending.put("U1", {"action": "add"})
    clock["now"] = 301
    assert pending.pop("U1") is None
