"""指令處理：登入／切換／新增／刪除／清單／今日資訊／線圖。"""
import logging
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from .chart import render_kline_png
from .deps import Deps
from .flex import (
    build_chart_bubble,
    build_chart_carousel_message,
    build_chart_message,
    build_picks_message,
    build_portfolio_message,
)
from .history import get_price_history, merge_realtime_bar
from .indicators import compute_indicators
from .market_health import build_market_health_message
from .parser import HELP_TEXT, MENU_ACTIONS, Command, aggregate_holdings, format_number, parse_command
from .screener import format_picks_message, run_daily_picks
from .webview import picks_sig, portfolio_sig, stock_sig

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


async def _apply_pick(deps: Deps, line_user_id: str, member: dict, cmd: Command, pending_item: dict) -> str | dict:
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


def _latest_by_stock(rows: list[dict]) -> dict[str, dict]:
    """rows 已依 trade_date 由新到舊排序，取每檔第一筆。"""
    latest: dict[str, dict] = {}
    for row in rows:
        latest.setdefault(str(row["stock_no"]), row)
    return latest


def portfolio_url(deps: Deps, member: dict) -> str:
    return f"{deps.base_url}/p/{member['id']}?sig={portfolio_sig(member['id'], deps.sign_key)}"


async def build_portfolio_entries(deps: Deps, member: dict) -> list[dict]:
    """持股完整資料（報價、指標、法人、資券），供 Flex 卡片與網頁版共用。"""
    rows = await deps.db.get(f"holdings?member_id=eq.{member['id']}&select=stock_no,shares,cost_price")
    if not rows:
        return []
    aggregated = aggregate_holdings(rows)
    codes = ",".join(quote(a["stock_no"]) for a in aggregated)
    stock_rows = await deps.db.get(f"stocks?stock_no=in.({codes})&{_STOCK_COLUMNS}")
    info_map = {s["stock_no"]: s for s in stock_rows}
    recent_limit = len(aggregated) * 8
    margin_map = _latest_by_stock(
        await deps.db.get(f"daily_margins?stock_no=in.({codes})&order=trade_date.desc&limit={recent_limit}")
    )
    institutional_map = _latest_by_stock(
        await deps.db.get(f"daily_institutional?stock_no=in.({codes})&order=trade_date.desc&limit={recent_limit}")
    )
    entries = []
    for agg in aggregated:
        info = info_map.get(agg["stock_no"], {})
        indicators = None
        history = []
        try:
            history = await get_price_history(deps.db, deps.twse, agg["stock_no"], info.get("market"))
            indicators = compute_indicators(history)
        except Exception:
            logger.warning("技術指標計算失敗 stock_no=%s", agg["stock_no"], exc_info=True)
        # 收盤價優先用自家資料庫（每日快照累積），避免逐檔打 TWSE 觸發限流
        if history:
            latest_quote = {"date": history[-1]["trade_date"], "close": history[-1]["close"]}
        else:
            latest_quote = await deps.twse.fetch_close(agg["stock_no"], info.get("market"))
        entries.append(
            {
                **agg,
                "name": info.get("name", agg["stock_no"]),
                "industry": info.get("industry"),
                "market": info.get("market"),
                "quote": latest_quote,
                "indicators": indicators,
                "margin": margin_map.get(agg["stock_no"]),
                "institutional": institutional_map.get(agg["stock_no"]),
            }
        )
    return entries


async def _handle_list(deps: Deps, member: dict) -> str | dict:
    entries = await build_portfolio_entries(deps, member)
    if not entries:
        return f"{member['name']} 目前沒有任何持股，輸入「新增2330」開始記錄。"
    return build_portfolio_message(member["name"], entries, portfolio_url(deps, member))


def stock_web_url(deps: Deps, stock_no: str) -> str:
    return f"{deps.base_url}/s/{stock_no}?sig={stock_sig(stock_no, deps.sign_key)}"


async def _render_chart_reply(deps: Deps, stock: dict) -> str | dict:
    history = await get_price_history(deps.db, deps.twse, stock["stock_no"], stock.get("market"))
    # 點圖當下抓 MIS 即時價拼上最後一根 K 棒（盤中即時、盤後為當日收盤）
    history = merge_realtime_bar(history, await deps.twse.fetch_realtime(stock["stock_no"], stock.get("market")))
    if len(history) < _MIN_CHART_ROWS:
        return f"❌ {_stock_label(stock)} 歷史資料不足，暫時畫不出線圖。"
    png = render_kline_png(history, f"{stock['stock_no']} {stock['name']}")
    chart_id = deps.charts.put(png)
    image_url = f"{deps.base_url}/charts/{chart_id}.png"
    indicators = compute_indicators(history)
    # 集保大戶比為週頻資料，僅於週六週報與個股網頁呈現，不放每日卡片
    return build_chart_message(
        stock,
        image_url,
        history[-1]["close"],
        indicators,
        page_url=stock_web_url(deps, stock["stock_no"]),
    )


_CAROUSEL_MAX_BUBBLES = 10  # LINE carousel 上限 12，保守取 10（回覆時間也較穩）


async def _handle_charts_all(deps: Deps, member: dict) -> str | dict:
    rows = await deps.db.get(f"holdings?member_id=eq.{member['id']}&select=stock_no")
    if not rows:
        return f"{member['name']} 目前沒有任何持股，輸入「新增2330」開始記錄。"
    codes = sorted({str(row["stock_no"]) for row in rows})
    truncated = len(codes) > _CAROUSEL_MAX_BUBBLES
    codes = codes[:_CAROUSEL_MAX_BUBBLES]
    codes_query = ",".join(quote(code) for code in codes)
    stock_rows = await deps.db.get(f"stocks?stock_no=in.({codes_query})&{_STOCK_COLUMNS}")
    info_map = {s["stock_no"]: s for s in stock_rows}

    bubbles = []
    skipped = []
    for code in codes:
        stock = info_map.get(code, {"stock_no": code, "name": code, "market": None})
        try:
            history = await get_price_history(deps.db, deps.twse, code, stock.get("market"))
            history = merge_realtime_bar(history, await deps.twse.fetch_realtime(code, stock.get("market")))
            if len(history) < _MIN_CHART_ROWS:
                skipped.append(code)
                continue
            png = render_kline_png(history, f"{code} {stock['name']}")
            chart_id = deps.charts.put(png)
            image_url = f"{deps.base_url}/charts/{chart_id}.png"
            bubbles.append(
                build_chart_bubble(
                    stock, image_url, history[-1]["close"], compute_indicators(history),
                    size="mega", page_url=stock_web_url(deps, code),
                )
            )
        except Exception:
            logger.warning("持股線圖產生失敗 stock_no=%s", code, exc_info=True)
            skipped.append(code)
    if not bubbles:
        return "❌ 目前持股都還畫不出線圖（歷史資料不足），過幾個交易日再試。"
    message = build_chart_carousel_message(member["name"], bubbles)
    if truncated or skipped:
        notes = []
        if truncated:
            notes.append(f"僅顯示前 {_CAROUSEL_MAX_BUBBLES} 檔")
        if skipped:
            notes.append(f"資料不足略過：{'、'.join(skipped)}")
        message["altText"] += f"（{'；'.join(notes)}）"
    return message


async def _handle_chart(deps: Deps, line_user_id: str, cmd: Command) -> str | dict:
    stock = await _resolve_stock(deps, cmd.stock)
    if not stock:
        return f"❌ 找不到「{cmd.stock}」。請確認名稱（公司簡稱），或直接輸入代號，例如：線圖2330"
    if stock.get("candidates"):
        deps.pending.put(line_user_id, {"action": "chart", "candidates": stock["candidates"]})
        return _candidates_reply(cmd.stock, stock["candidates"])
    return await _render_chart_reply(deps, stock)


_VOLUME_RANK_MIN_SHARES = 1_000_000  # 過濾冷門股：今日至少 1,000 張
_VOLUME_RANK_LIMIT = 10


async def _handle_volume_rank(deps: Deps) -> str:
    rows = await deps.db.rpc(
        "volume_surge_ranking", {"min_volume": _VOLUME_RANK_MIN_SHARES, "limit_n": _VOLUME_RANK_LIMIT}
    )
    if not rows:
        return "目前資料不足，還排不出量增排行（每個交易日收盤後更新）。"
    codes_query = ",".join(quote(str(r["stock_no"])) for r in rows)
    stock_rows = await deps.db.get(f"stocks?stock_no=in.({codes_query})&select=stock_no,name")
    name_map = {s["stock_no"]: s["name"] for s in stock_rows}

    latest_date = max(str(r["trade_date"]) for r in rows)
    lines = [f"📈 量增排行（{latest_date[5:].replace('-', '/')}）"]
    for i, row in enumerate(rows, start=1):
        volume = float(row["volume"])
        ratio = volume / float(row["prev_volume"])
        close = float(row["close"])
        prev_close = float(row["prev_close"]) if row.get("prev_close") else None
        pct_text = ""
        if prev_close:
            pct = (close - prev_close) / prev_close * 100
            pct_text = f"（{'+' if pct >= 0 else ''}{pct:.1f}%）"
        name = name_map.get(str(row["stock_no"]), "")
        lines.append(f"{i}. {row['stock_no']} {name}")
        lines.append(f"　{format_number(volume / 1000)} 張・前日 ×{ratio:.1f}｜收 {format_number(close)}{pct_text}")
    return "\n".join(lines)


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


_BATCH_MAX_LINES = 30


async def handle_text(deps: Deps, line_user_id: str | None, text: str) -> str | dict:
    """單行走一般指令；多行訊息逐行執行（批次新增/刪除用）。"""
    lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    if len(lines) <= 1:
        return await handle_command(deps, line_user_id, parse_command(text))
    replies = []
    for line in lines[:_BATCH_MAX_LINES]:
        result = await handle_command(deps, line_user_id, parse_command(line))
        replies.append(result if isinstance(result, str) else "（卡片類指令請單獨輸入）")
    if len(lines) > _BATCH_MAX_LINES:
        replies.append(f"⚠️ 一次最多處理 {_BATCH_MAX_LINES} 行，其餘 {len(lines) - _BATCH_MAX_LINES} 行未執行")
    return "\n".join(replies)


async def _handle_clear(deps: Deps, line_user_id: str, member: dict) -> str:
    rows = await deps.db.get(f"holdings?member_id=eq.{member['id']}&select=id")
    if not rows:
        return f"{member['name']} 目前沒有任何持股紀錄。"
    deps.pending.put(line_user_id, {"action": "clear_all", "member_id": member["id"]})
    return f"⚠️ 即將刪除「{member['name']}」的全部 {len(rows)} 筆持股紀錄，回覆「確認」執行（5 分鐘內有效）。"


async def _handle_confirm(deps: Deps, line_user_id: str, member: dict) -> str:
    pending_item = deps.pending.pop(line_user_id)
    if not pending_item or pending_item.get("action") != "clear_all":
        return "目前沒有待確認的操作。"
    if pending_item.get("member_id") != member["id"]:
        return "身份已切換，已取消清空操作。"
    deleted = await deps.db.delete(f"holdings?member_id=eq.{member['id']}")
    return f"🗑 已清空「{member['name']}」的持股，共刪除 {len(deleted)} 筆。"


async def handle_command(deps: Deps, line_user_id: str | None, cmd: Command) -> str | dict:
    if not line_user_id:
        return HELP_TEXT
    if cmd.action == "login":
        return await _handle_login(deps, line_user_id, cmd.name)
    if cmd.action == "switch":
        return await _handle_switch(deps, line_user_id, cmd.name)
    if cmd.action == "help":
        return HELP_TEXT
    if cmd.action == "volume_rank":  # 全市場排行，不需身份
        return await _handle_volume_rank(deps)
    if cmd.action == "picks":  # 全市場選股，不需身份
        return await _handle_picks(deps)
    if cmd.action == "health":  # 大盤體檢，不需身份
        return await build_market_health_message(deps)
    member = await _get_acting_member(deps, line_user_id)
    if not member:
        return "👋 請先輸入「登入你的名字」開始使用，例如：登入dada"
    if cmd.action == "pick":
        pending_item = deps.pending.pop(line_user_id)
        if pending_item:
            return await _apply_pick(deps, line_user_id, member, cmd, pending_item)
        menu_action = MENU_ACTIONS.get(cmd.index)
        if menu_action is None:
            return HELP_TEXT
        cmd = Command(action=menu_action)  # 數字當功能選單捷徑
    if cmd.action == "add":
        return await _handle_add(deps, line_user_id, member, cmd)
    if cmd.action == "remove":
        return await _handle_remove(deps, line_user_id, member, cmd.stock)
    if cmd.action == "list":
        return await _handle_list(deps, member)
    if cmd.action == "news":
        return await _handle_news(deps, member)
    if cmd.action == "chart":
        return await _handle_chart(deps, line_user_id, cmd)
    if cmd.action == "charts_all":
        return await _handle_charts_all(deps, member)
    if cmd.action == "clear":
        return await _handle_clear(deps, line_user_id, member)
    if cmd.action == "confirm":
        return await _handle_confirm(deps, line_user_id, member)
    if cmd.action == "volume_rank":
        return await _handle_volume_rank(deps)
    if cmd.action == "picks":
        return await _handle_picks(deps)
    if cmd.action == "health":
        return await build_market_health_message(deps)
    return HELP_TEXT


def picks_web_url(deps: Deps) -> str:
    return f"{deps.base_url}/picks?sig={picks_sig(deps.sign_key)}"


async def _handle_picks(deps: Deps) -> dict:
    result = await run_daily_picks(deps)
    return build_picks_message(result, format_picks_message(result), picks_web_url(deps))
