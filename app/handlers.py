"""指令處理：登入／切換／新增／刪除／清單／今日資訊／線圖。"""
import logging
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from .chart import render_kline_png
from .deps import Deps
from .flex import build_chart_message, build_portfolio_message
from .history import get_price_history
from .indicators import compute_indicators
from .parser import HELP_TEXT, Command, aggregate_holdings, format_number

logger = logging.getLogger(__name__)

_STOCK_NO_PATTERN = re.compile(r"\d{4,6}[A-Z]?")
_STOCK_COLUMNS = "select=stock_no,name,industry,market"
_TAIPEI_TZ = timezone(timedelta(hours=8))
_NEWS_SUBJECT_MAX = 120
_MIN_CHART_ROWS = 5


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _get_member_by_name(deps: Deps, name: str) -> dict | None:
    rows = await deps.db.get(f"members?name=eq.{quote(name)}&select=id,name")
    return rows[0] if rows else None


async def _get_acting_member(deps: Deps, line_user_id: str) -> dict | None:
    bindings = await deps.db.get(
        f"line_bindings?line_user_id=eq.{quote(line_user_id)}&select=member_id,acting_member_id"
    )
    if not bindings:
        return None
    member_id = bindings[0].get("acting_member_id") or bindings[0].get("member_id")
    if not member_id:
        return None
    members = await deps.db.get(f"members?id=eq.{member_id}&select=id,name")
    return members[0] if members else None


async def _handle_login(deps: Deps, line_user_id: str, name: str) -> str:
    member = await _get_member_by_name(deps, name)
    if not member:
        created = await deps.db.insert("members", {"name": name})
        member = created[0]
    await deps.db.insert(
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


async def _handle_switch(deps: Deps, line_user_id: str, name: str) -> str:
    member = await _get_member_by_name(deps, name)
    if not member:
        return f"❌ 找不到成員「{name}」，請先輸入「登入{name}」建立身份。"
    bindings = await deps.db.get(f"line_bindings?line_user_id=eq.{quote(line_user_id)}&select=line_user_id")
    if not bindings:
        return "❌ 請先輸入「登入你的名字」完成綁定，再切換身份。"
    await deps.db.patch(
        f"line_bindings?line_user_id=eq.{quote(line_user_id)}",
        {"acting_member_id": member["id"], "updated_at": _now_iso()},
    )
    return f"🔁 已切換為「{member['name']}」，之後的新增/查詢都作用在這個帳戶。"


async def _resolve_stock(deps: Deps, user_input: str) -> dict | None:
    if _STOCK_NO_PATTERN.fullmatch(user_input):
        rows = await deps.db.get(f"stocks?stock_no=eq.{quote(user_input)}&{_STOCK_COLUMNS}")
        if rows:
            return rows[0]
        # 自動補齊尾碼：如 00631 → 00631L；唯一符合就採用，多個則請使用者選
        candidates = await deps.db.get(
            f"stocks?stock_no=like.{quote(user_input)}*&{_STOCK_COLUMNS}&order=stock_no&limit=6"
        )
        if len(candidates) == 1:
            return candidates[0]
        if candidates:
            return {"candidates": candidates}
        # 查不到仍允許新增（可能是極新掛牌），名稱先以代號代替
        return {"stock_no": user_input, "name": user_input, "market": None, "unknown": True}
    rows = await deps.db.get(f"stocks?name=eq.{quote(user_input)}&{_STOCK_COLUMNS}")
    return rows[0] if rows else None


def _candidates_reply(user_input: str, candidates: list[dict]) -> str:
    lines = [f"「{user_input}」有多個符合，請回覆數字選擇（5 分鐘內有效）："]
    lines += [f"{i}. {c['stock_no']} {c['name']}" for i, c in enumerate(candidates, start=1)]
    return "\n".join(lines)


def _stock_label(stock: dict) -> str:
    market = f"・{stock['market']}" if stock.get("market") else ""
    return f"{stock['name']}（{stock['stock_no']}{market}）"


async def _insert_holding(deps: Deps, member: dict, stock: dict, shares: float | None, cost: float | None) -> str:
    await deps.db.insert(
        "holdings",
        {
            "member_id": member["id"],
            "stock_no": stock["stock_no"],
            "shares": shares if shares is not None else 0,
            "cost_price": cost,
        },
    )
    if shares is None:
        detail = "（觀察，未記股數）"
    else:
        cost_text = "" if cost is None else f"＠{format_number(cost)}"
        detail = f"{format_number(shares)} 股{cost_text}"
    warning = "\n⚠️ 代號不在上市/上櫃公司對照表中（可能為 ETF），將自動嘗試兩邊的報價" if stock.get("unknown") else ""
    return f"✅ 已為 {member['name']} 新增 {_stock_label(stock)}{detail}{warning}"


async def _delete_holding(deps: Deps, member: dict, stock: dict | None, stock_input: str) -> str:
    stock_no = stock["stock_no"] if stock else stock_input
    deleted = await deps.db.delete(f"holdings?member_id=eq.{member['id']}&stock_no=eq.{quote(stock_no)}")
    if not deleted:
        return f"❌ {member['name']} 沒有「{stock_input}」的紀錄。"
    label = _stock_label(stock) if stock else stock_no
    return f"🗑 已刪除 {member['name']} 的 {label}，共 {len(deleted)} 筆。"


async def _handle_add(deps: Deps, line_user_id: str, member: dict, cmd: Command) -> str:
    stock = await _resolve_stock(deps, cmd.stock)
    if not stock:
        return f"❌ 找不到「{cmd.stock}」。請確認名稱（公司簡稱），或直接輸入代號，例如：新增2330"
    if stock.get("candidates"):
        deps.pending.put(
            line_user_id,
            {"action": "add", "candidates": stock["candidates"], "shares": cmd.shares, "cost": cmd.cost},
        )
        return _candidates_reply(cmd.stock, stock["candidates"])
    return await _insert_holding(deps, member, stock, cmd.shares, cmd.cost)


async def _handle_remove(deps: Deps, line_user_id: str, member: dict, stock_input: str) -> str:
    stock = await _resolve_stock(deps, stock_input)
    if stock and stock.get("candidates"):
        deps.pending.put(line_user_id, {"action": "remove", "candidates": stock["candidates"]})
        return _candidates_reply(stock_input, stock["candidates"])
    return await _delete_holding(deps, member, stock, stock_input)


async def _handle_pick(deps: Deps, line_user_id: str, member: dict, cmd: Command) -> str | dict:
    pending_item = deps.pending.pop(line_user_id)
    if not pending_item:
        return "目前沒有等待選擇的項目（可能已過期），請重新輸入指令。"
    candidates = pending_item["candidates"]
    if cmd.index > len(candidates):
        deps.pending.put(line_user_id, pending_item)
        return f"請輸入 1～{len(candidates)} 之間的數字。"
    stock = candidates[cmd.index - 1]
    if pending_item["action"] == "add":
        return await _insert_holding(deps, member, stock, pending_item.get("shares"), pending_item.get("cost"))
    if pending_item["action"] == "chart":
        return await _render_chart_reply(deps, stock)
    return await _delete_holding(deps, member, stock, stock["stock_no"])


async def _handle_list(deps: Deps, member: dict) -> str | dict:
    rows = await deps.db.get(f"holdings?member_id=eq.{member['id']}&select=stock_no,shares,cost_price")
    if not rows:
        return f"{member['name']} 目前沒有任何持股，輸入「新增2330」開始記錄。"
    aggregated = aggregate_holdings(rows)
    codes = ",".join(quote(a["stock_no"]) for a in aggregated)
    stock_rows = await deps.db.get(f"stocks?stock_no=in.({codes})&{_STOCK_COLUMNS}")
    info_map = {s["stock_no"]: s for s in stock_rows}
    entries = []
    for agg in aggregated:
        info = info_map.get(agg["stock_no"], {})
        indicators = None
        try:
            history = await get_price_history(deps.db, deps.twse, agg["stock_no"], info.get("market"))
            indicators = compute_indicators(history)
        except Exception:
            logger.warning("技術指標計算失敗 stock_no=%s", agg["stock_no"], exc_info=True)
        entries.append(
            {
                **agg,
                "name": info.get("name", agg["stock_no"]),
                "industry": info.get("industry"),
                "market": info.get("market"),
                "quote": await deps.twse.fetch_close(agg["stock_no"], info.get("market")),
                "indicators": indicators,
            }
        )
    return build_portfolio_message(member["name"], entries)


async def _render_chart_reply(deps: Deps, stock: dict) -> str | dict:
    history = await get_price_history(deps.db, deps.twse, stock["stock_no"], stock.get("market"))
    if len(history) < _MIN_CHART_ROWS:
        return f"❌ {_stock_label(stock)} 歷史資料不足，暫時畫不出線圖。"
    png = render_kline_png(history, f"{stock['stock_no']} {stock['name']}")
    chart_id = deps.charts.put(png)
    image_url = f"{deps.base_url}/charts/{chart_id}.png"
    indicators = compute_indicators(history)
    return build_chart_message(stock, image_url, history[-1]["close"], indicators)


async def _handle_chart(deps: Deps, line_user_id: str, cmd: Command) -> str | dict:
    stock = await _resolve_stock(deps, cmd.stock)
    if not stock:
        return f"❌ 找不到「{cmd.stock}」。請確認名稱（公司簡稱），或直接輸入代號，例如：線圖2330"
    if stock.get("candidates"):
        deps.pending.put(line_user_id, {"action": "chart", "candidates": stock["candidates"]})
        return _candidates_reply(cmd.stock, stock["candidates"])
    return await _render_chart_reply(deps, stock)


def _news_subject(item: dict) -> str:
    subject = str(item.get("主旨 ") or item.get("主旨") or "").strip().replace("\r\n", "")
    return subject[:_NEWS_SUBJECT_MAX] + ("…" if len(subject) > _NEWS_SUBJECT_MAX else "")


def _filter_news(items: list[dict], code_key: str, codes: set[str]) -> list[dict]:
    return [item for item in items or [] if str(item.get(code_key, "")).strip() in codes]


async def _handle_news(deps: Deps, member: dict) -> str:
    rows = await deps.db.get(f"holdings?member_id=eq.{member['id']}&select=stock_no")
    if not rows:
        return f"{member['name']} 目前沒有任何持股，輸入「新增2330」開始記錄。"
    codes = {str(row["stock_no"]) for row in rows}
    codes_query = ",".join(quote(code) for code in sorted(codes))
    stock_rows = await deps.db.get(f"stocks?stock_no=in.({codes_query})&select=stock_no,name")
    name_map = {s["stock_no"]: s["name"] for s in stock_rows}

    news: list[dict] = []
    try:
        news += _filter_news(await deps.twse.fetch_listed_news(), "公司代號", codes)
    except Exception:
        logger.warning("上市重大訊息查詢失敗", exc_info=True)
    try:
        news += _filter_news(await deps.twse.fetch_otc_news(), "SecuritiesCompanyCode", codes)
    except Exception:
        logger.warning("上櫃重大訊息查詢失敗", exc_info=True)

    today = datetime.now(_TAIPEI_TZ).date().isoformat()
    dividends = await deps.db.get(
        f"dividend_events?stock_no=in.({codes_query})&ex_date=gte.{today}&order=ex_date&limit=10"
    )

    lines = [f"📢 {member['name']} 持股今日資訊"]
    lines.append("【重大訊息】")
    if news:
        for item in news:
            code = str(item.get("公司代號") or item.get("SecuritiesCompanyCode") or "").strip()
            lines.append(f"・{code} {name_map.get(code, '')}：{_news_subject(item)}")
    else:
        lines.append("今日無持股相關重大訊息")
    lines.append("")
    lines.append("【即將除權息】")
    if dividends:
        for event in dividends:
            code = event["stock_no"]
            cash = event.get("cash_dividend")
            cash_text = f"，現金股利 {format_number(cash)} 元" if cash else ""
            lines.append(f"・{code} {name_map.get(code, '')}：{event['ex_date']} 除{event.get('kind') or ''}{cash_text}")
    else:
        lines.append("近期無持股除權息")
    return "\n".join(lines)


async def handle_command(deps: Deps, line_user_id: str | None, cmd: Command) -> str | dict:
    if not line_user_id:
        return HELP_TEXT
    if cmd.action == "login":
        return await _handle_login(deps, line_user_id, cmd.name)
    if cmd.action == "switch":
        return await _handle_switch(deps, line_user_id, cmd.name)
    if cmd.action == "help":
        return HELP_TEXT
    member = await _get_acting_member(deps, line_user_id)
    if not member:
        return "👋 請先輸入「登入你的名字」開始使用，例如：登入dada"
    if cmd.action == "add":
        return await _handle_add(deps, line_user_id, member, cmd)
    if cmd.action == "remove":
        return await _handle_remove(deps, line_user_id, member, cmd.stock)
    if cmd.action == "pick":
        return await _handle_pick(deps, line_user_id, member, cmd)
    if cmd.action == "list":
        return await _handle_list(deps, member)
    if cmd.action == "news":
        return await _handle_news(deps, member)
    if cmd.action == "chart":
        return await _handle_chart(deps, line_user_id, cmd)
    return HELP_TEXT
