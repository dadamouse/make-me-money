"""週末推播：週六持股週報（本週回顧）、週日下週展望（除權息＋財報期限）。"""
import logging
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote

from .deps import Deps
from .parser import aggregate_holdings, format_number, sign_of

logger = logging.getLogger(__name__)

_TAIPEI_TZ = timezone(timedelta(hours=8))
_WEEK_WINDOW_DAYS = 8

# 法定財報期限（月, 日, 說明）
REPORT_DEADLINES = (
    (3, 31, "年報申報期限"),
    (5, 15, "Q1 財報申報期限"),
    (8, 14, "Q2 財報申報期限"),
    (11, 14, "Q3 財報申報期限"),
)


def _taipei_today() -> date:
    return datetime.now(_TAIPEI_TZ).date()


def upcoming_deadlines(today: date, days: int = 7) -> list[str]:
    """未來 N 天內的法定財報／營收期限提醒。"""
    found = []
    for offset in range(1, days + 1):
        day = today + timedelta(days=offset)
        for month, dom, label in REPORT_DEADLINES:
            if day.month == month and day.day == dom:
                found.append(f"{day.month}/{day.day} {label}")
        if day.day == 10:
            found.append(f"{day.month}/{day.day} 各公司{day.month}月營收公布期限")
    return found


async def _holdings_with_names(deps: Deps, member: dict) -> tuple[list[dict], dict]:
    rows = await deps.db.get(f"holdings?member_id=eq.{member['id']}&select=stock_no,shares,cost_price")
    if not rows:
        return [], {}
    aggregated = aggregate_holdings(rows)
    codes_query = ",".join(quote(a["stock_no"]) for a in aggregated)
    stock_rows = await deps.db.get(f"stocks?stock_no=in.({codes_query})&select=stock_no,name")
    return aggregated, {s["stock_no"]: s["name"] for s in stock_rows}


async def build_weekly_report(deps: Deps, member: dict) -> str | None:
    """本週回顧：各持股週漲跌、總市值變化、法人本週買賣超。"""
    aggregated, name_map = await _holdings_with_names(deps, member)
    if not aggregated:
        return None
    codes_query = ",".join(quote(a["stock_no"]) for a in aggregated)
    today = _taipei_today()
    start = (today - timedelta(days=_WEEK_WINDOW_DAYS)).isoformat()

    closes = await deps.db.get(
        f"daily_closes?stock_no=in.({codes_query})&trade_date=gte.{start}"
        f"&select=stock_no,trade_date,close&order=trade_date"
    )
    span: dict[str, dict] = {}
    for row in closes:
        code = str(row["stock_no"])
        close = float(row["close"])
        entry = span.setdefault(code, {"first": close, "last": close})
        entry["last"] = close

    institutional = await deps.db.get(
        f"daily_institutional?stock_no=in.({codes_query})&trade_date=gte.{start}"
        f"&select=stock_no,foreign_net,trust_net"
    )
    flows: dict[str, dict] = {}
    for row in institutional:
        code = str(row["stock_no"])
        flow = flows.setdefault(code, {"foreign": 0.0, "trust": 0.0})
        flow["foreign"] += float(row.get("foreign_net") or 0)
        flow["trust"] += float(row.get("trust_net") or 0)

    label = f"{(today - timedelta(days=_WEEK_WINDOW_DAYS)).strftime('%m/%d')}〜{today.strftime('%m/%d')}"
    lines = [f"📈 {member['name']} 的持股週報（{label}）"]
    total_first = total_last = 0.0
    for agg in aggregated:
        code = agg["stock_no"]
        name = name_map.get(code, code)
        prices = span.get(code)
        if not prices:
            lines.append(f"・{code} {name}：本週無報價資料")
            continue
        pct = (prices["last"] - prices["first"]) / prices["first"] * 100 if prices["first"] else 0
        lines.append(
            f"・{code} {name}：{format_number(prices['first'])} → {format_number(prices['last'])}"
            f"（{sign_of(pct)}{pct:.1f}%）"
        )
        if agg["shares"] > 0:
            total_first += agg["shares"] * prices["first"]
            total_last += agg["shares"] * prices["last"]
    if total_first > 0:
        total_pct = (total_last - total_first) / total_first * 100
        lines.append(f"─────\n總市值 {format_number(total_last)}（本週 {sign_of(total_pct)}{total_pct:.1f}%）")

    flow_lines = []
    for agg in aggregated:
        flow = flows.get(agg["stock_no"])
        if not flow or (flow["foreign"] == 0 and flow["trust"] == 0):
            continue
        parts = []
        if flow["foreign"]:
            parts.append(f"外資 {sign_of(flow['foreign'])}{format_number(round(flow['foreign'] / 1000))} 張")
        if flow["trust"]:
            parts.append(f"投信 {sign_of(flow['trust'])}{format_number(round(flow['trust'] / 1000))} 張")
        flow_lines.append(f"・{agg['stock_no']} {name_map.get(agg['stock_no'], '')}：{'｜'.join(parts)}")
    if flow_lines:
        lines.append("")
        lines.append("【法人本週動向】")
        lines += flow_lines
    return "\n".join(lines)


async def build_weekly_outlook(deps: Deps, member: dict) -> str | None:
    """下週展望：持股除權息＋法定財報期限。"""
    aggregated, name_map = await _holdings_with_names(deps, member)
    if not aggregated:
        return None
    codes_query = ",".join(quote(a["stock_no"]) for a in aggregated)
    today = _taipei_today()
    week_end = (today + timedelta(days=7)).isoformat()
    dividends = await deps.db.get(
        f"dividend_events?stock_no=in.({codes_query})&ex_date=gte.{today.isoformat()}"
        f"&ex_date=lte.{week_end}&order=ex_date"
    )
    lines = [f"📅 {member['name']} 的下週展望"]
    lines.append("【下週除權息】")
    if dividends:
        for event in dividends:
            code = event["stock_no"]
            cash = event.get("cash_dividend")
            cash_text = f"，現金股利 {format_number(float(cash))} 元" if cash else ""
            lines.append(f"・{code} {name_map.get(code, '')}：{event['ex_date']} 除{event.get('kind') or ''}{cash_text}")
    else:
        lines.append("下週無持股除權息")
    deadlines = upcoming_deadlines(today)
    if deadlines:
        lines.append("")
        lines.append("【財報行事曆】")
        lines += [f"・{item}" for item in deadlines]
    return "\n".join(lines)
