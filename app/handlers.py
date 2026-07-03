"""指令處理：登入／切換／新增／刪除／清單。"""
import re
from datetime import datetime, timezone
from urllib.parse import quote

from .flex import build_portfolio_message
from .parser import HELP_TEXT, Command, aggregate_holdings, format_number
from .supabase import SupabaseClient
from .twse import TwseClient

_STOCK_NO_PATTERN = re.compile(r"\d{4,6}[A-Z]?")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _get_member_by_name(db: SupabaseClient, name: str) -> dict | None:
    rows = await db.get(f"members?name=eq.{quote(name)}&select=id,name")
    return rows[0] if rows else None


async def _get_acting_member(db: SupabaseClient, line_user_id: str) -> dict | None:
    bindings = await db.get(f"line_bindings?line_user_id=eq.{quote(line_user_id)}&select=member_id,acting_member_id")
    if not bindings:
        return None
    member_id = bindings[0].get("acting_member_id") or bindings[0].get("member_id")
    if not member_id:
        return None
    members = await db.get(f"members?id=eq.{member_id}&select=id,name")
    return members[0] if members else None


async def _handle_login(db: SupabaseClient, line_user_id: str, name: str) -> str:
    member = await _get_member_by_name(db, name)
    if not member:
        created = await db.insert("members", {"name": name})
        member = created[0]
    await db.insert(
        "line_bindings?on_conflict=line_user_id",
        {
            "line_user_id": line_user_id,
            "member_id": member["id"],
            "acting_member_id": member["id"],
            "updated_at": _now_iso(),
        },
        prefer="return=representation,resolution=merge-duplicates",
    )
    return f"✅ 已登入「{member['name']}」，之後的操作都會記在這個身份。"


async def _handle_switch(db: SupabaseClient, line_user_id: str, name: str) -> str:
    member = await _get_member_by_name(db, name)
    if not member:
        return f"❌ 找不到成員「{name}」，請先輸入「登入{name}」建立身份。"
    bindings = await db.get(f"line_bindings?line_user_id=eq.{quote(line_user_id)}&select=line_user_id")
    if not bindings:
        return "❌ 請先輸入「登入你的名字」完成綁定，再切換身份。"
    await db.patch(
        f"line_bindings?line_user_id=eq.{quote(line_user_id)}",
        {"acting_member_id": member["id"], "updated_at": _now_iso()},
    )
    return f"🔁 已切換為「{member['name']}」，之後的新增/查詢都作用在這個帳戶。"


async def _resolve_stock(db: SupabaseClient, user_input: str) -> dict | None:
    if _STOCK_NO_PATTERN.fullmatch(user_input):
        rows = await db.get(f"stocks?stock_no=eq.{quote(user_input)}&select=stock_no,name")
        # 代號查不到對照表仍允許新增（可能是上櫃股），名稱先以代號代替
        return rows[0] if rows else {"stock_no": user_input, "name": user_input, "unknown": True}
    rows = await db.get(f"stocks?name=eq.{quote(user_input)}&select=stock_no,name")
    return rows[0] if rows else None


async def _handle_add(db: SupabaseClient, member: dict, cmd: Command) -> str:
    stock = await _resolve_stock(db, cmd.stock)
    if not stock:
        return f"❌ 找不到「{cmd.stock}」。請確認名稱（公司簡稱），或直接輸入代號，例如：新增2330"
    await db.insert(
        "holdings",
        {
            "member_id": member["id"],
            "stock_no": stock["stock_no"],
            "shares": cmd.shares if cmd.shares is not None else 0,
            "cost_price": cmd.cost,
        },
    )
    if cmd.shares is None:
        detail = "（觀察，未記股數）"
    else:
        cost_text = "" if cmd.cost is None else f"＠{format_number(cmd.cost)}"
        detail = f"{format_number(cmd.shares)} 股{cost_text}"
    warning = "\n⚠️ 代號不在上市對照表中，報價可能查不到（上櫃股票暫不支援）" if stock.get("unknown") else ""
    return f"✅ 已為 {member['name']} 新增 {stock['name']}（{stock['stock_no']}）{detail}{warning}"


async def _handle_remove(db: SupabaseClient, member: dict, stock_input: str) -> str:
    stock = await _resolve_stock(db, stock_input)
    stock_no = stock["stock_no"] if stock else stock_input
    deleted = await db.delete(f"holdings?member_id=eq.{member['id']}&stock_no=eq.{quote(stock_no)}")
    if not deleted:
        return f"❌ {member['name']} 沒有「{stock_input}」的紀錄。"
    label = f"{stock['name']}（{stock_no}）" if stock else stock_no
    return f"🗑 已刪除 {member['name']} 的 {label}，共 {len(deleted)} 筆。"


async def _handle_list(db: SupabaseClient, twse: TwseClient, member: dict) -> str | dict:
    rows = await db.get(f"holdings?member_id=eq.{member['id']}&select=stock_no,shares,cost_price")
    if not rows:
        return f"{member['name']} 目前沒有任何持股，輸入「新增2330」開始記錄。"
    aggregated = aggregate_holdings(rows)
    codes = ",".join(quote(a["stock_no"]) for a in aggregated)
    stock_rows = await db.get(f"stocks?stock_no=in.({codes})&select=stock_no,name")
    name_map = {s["stock_no"]: s["name"] for s in stock_rows}
    entries = [
        {
            **agg,
            "name": name_map.get(agg["stock_no"], agg["stock_no"]),
            "quote": await twse.fetch_close(agg["stock_no"]),
        }
        for agg in aggregated
    ]
    return build_portfolio_message(member["name"], entries)


async def handle_command(db: SupabaseClient, twse: TwseClient, line_user_id: str | None, cmd: Command) -> str | dict:
    if not line_user_id:
        return HELP_TEXT
    if cmd.action == "login":
        return await _handle_login(db, line_user_id, cmd.name)
    if cmd.action == "switch":
        return await _handle_switch(db, line_user_id, cmd.name)
    if cmd.action == "help":
        return HELP_TEXT
    member = await _get_acting_member(db, line_user_id)
    if not member:
        return "👋 請先輸入「登入你的名字」開始使用，例如：登入dada"
    if cmd.action == "add":
        return await _handle_add(db, member, cmd)
    if cmd.action == "remove":
        return await _handle_remove(db, member, cmd.stock)
    if cmd.action == "list":
        return await _handle_list(db, twse, member)
    return HELP_TEXT
