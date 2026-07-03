"""「我的股票」的 LINE Flex Message 版面。"""
from .parser import format_number, format_portfolio, format_roc_date, sign_of, summarize_portfolio

GAIN_COLOR = "#E53935"  # 台股慣例：紅漲
LOSS_COLOR = "#43A047"  # 綠跌
TITLE_COLOR = "#333333"
MUTED_COLOR = "#9E9E9E"
VALUE_COLOR = "#555555"
HEADER_BG = "#27346A"
HEADER_SUB_COLOR = "#B8C4E0"
INDUSTRY_COLOR = "#5C6BC0"
FALLBACK_INDUSTRY = "其他"
_ALT_TEXT_MAX = 400  # LINE altText 上限


def _text(content: str, **attrs) -> dict:
    return {"type": "text", "text": content, **attrs}


def _row(left: dict, right: dict) -> dict:
    return {"type": "box", "layout": "horizontal", "contents": [left, right]}


def _pnl_color(pnl: float) -> str:
    return GAIN_COLOR if pnl >= 0 else LOSS_COLOR


def _stock_block(item: dict) -> dict:
    quote = item.get("quote")
    market_prefix = f"{item['market']}｜" if item.get("market") else ""
    rows = [
        _row(
            _text(f"{item['stock_no']} {item['name']}", size="sm", weight="bold", color=TITLE_COLOR, flex=5, wrap=True),
            _text(
                format_number(quote["close"]) if quote else "查無報價",
                size="sm",
                align="end",
                color=TITLE_COLOR if quote else MUTED_COLOR,
                flex=3,
            ),
        )
    ]
    if not quote:
        if market_prefix:
            rows.append(_text(item["market"], size="xs", color=MUTED_COLOR))
        return {"type": "box", "layout": "vertical", "spacing": "xs", "contents": rows}
    if item["value"] is not None:
        rows.append(
            _row(
                _text(f"{market_prefix}{format_number(item['shares'])} 股", size="xs", color=MUTED_COLOR, flex=4),
                _text(f"市值 {format_number(item['value'])}", size="xs", color=VALUE_COLOR, align="end", flex=6),
            )
        )
        if item["pnl"] is not None:
            sign = sign_of(item["pnl"])
            rows.append(
                _text(
                    f"{sign}{format_number(item['pnl'])}（{sign}{item['pct']:.1f}%）",
                    size="xs",
                    align="end",
                    color=_pnl_color(item["pnl"]),
                )
            )
    else:
        rows.append(_text(f"{market_prefix}觀察中（未記股數）", size="xs", color=MUTED_COLOR))
    rows += _indicator_rows(item, quote["close"])
    rows += _flow_rows(item)
    return {"type": "box", "layout": "vertical", "spacing": "xs", "contents": rows}


def _signed(value: float) -> str:
    rounded = round(value)
    return f"{'+' if rounded >= 0 else ''}{format_number(rounded)}"


def _signed_lots(shares_value: float) -> str:
    """買賣超股數 → 帶正負號的張數。"""
    return _signed(shares_value / 1000)


def _flow_rows(item: dict) -> list[dict]:
    """三大法人買賣超與融資融券各一行；沒資料就省略。"""
    rows = []
    institutional = item.get("institutional")
    if institutional and institutional.get("total_net") is not None:
        parts = [
            f"{label}{_signed_lots(value)}"
            for label, value in (
                ("外資", institutional.get("foreign_net")),
                ("投信", institutional.get("trust_net")),
                ("自營", institutional.get("dealer_net")),
            )
            if value is not None
        ]
        total = institutional["total_net"]
        rows.append(
            _text(f"法人 {_signed_lots(total)} 張（{'｜'.join(parts)}）", size="xxs", color=_pnl_color(total))
        )
    margin = item.get("margin")
    if margin and margin.get("margin_balance") is not None:
        margin_change = margin.get("margin_change")
        short_balance = margin.get("short_balance")
        text = f"融資 {format_number(margin['margin_balance'])} 張"
        if margin_change is not None:
            text += f"（{_signed(margin_change)}）"
        if short_balance is not None:
            text += f"｜融券 {format_number(short_balance)} 張"
            short_change = margin.get("short_change")
            if short_change is not None:
                text += f"（{_signed(short_change)}）"
        rows.append(_text(text, size="xxs", color=MUTED_COLOR))
    return rows


def _indicator_rows(item: dict, close: float) -> list[dict]:
    """技術指標兩行：均線（含站上↑跌破↓）與 RSI/KD。資料不足的指標自動省略。"""
    ind = item.get("indicators") or {}
    rows = []
    ma_parts = [
        f"MA{n} {format_number(value)}{'↑' if close >= value else '↓'}"
        for n, value in ((5, ind.get("ma5")), (20, ind.get("ma20")), (60, ind.get("ma60")))
        if value is not None
    ]
    if ma_parts:
        rows.append(_text("｜".join(ma_parts), size="xxs", color=MUTED_COLOR))
    osc_parts = []
    if ind.get("rsi14") is not None:
        osc_parts.append(f"RSI {ind['rsi14']:.0f}")
    if ind.get("k") is not None and ind.get("d") is not None:
        kd_text = f"K {ind['k']:.0f}／D {ind['d']:.0f}"
        if ind.get("j") is not None:
            kd_text += f"／J {ind['j']:.0f}"
        osc_parts.append(kd_text)
    if osc_parts:
        rows.append(_text("｜".join(osc_parts), size="xxs", color=MUTED_COLOR))
    return rows


def _header(member_name: str, date_label: str | None) -> dict:
    contents = [_text(f"📊 {member_name} 的持股", size="lg", weight="bold", color="#FFFFFF")]
    if date_label:
        contents.append(_text(f"收盤日 {date_label}", size="xs", color=HEADER_SUB_COLOR, margin="xs"))
    return {"type": "box", "layout": "vertical", "backgroundColor": HEADER_BG, "paddingAll": "16px", "contents": contents}


def _footer(summary: dict) -> dict:
    rows = [
        _row(
            _text("總市值", size="sm", color=MUTED_COLOR, flex=3),
            _text(format_number(summary["total_value"]), size="sm", weight="bold", color=TITLE_COLOR, align="end", flex=5),
        )
    ]
    total_pnl = summary["total_pnl"]
    if total_pnl is not None:
        sign = sign_of(total_pnl)
        pct = total_pnl / summary["total_cost"] * 100
        rows.append(
            _row(
                _text("總損益", size="sm", color=MUTED_COLOR, flex=3),
                _text(
                    f"{sign}{format_number(total_pnl)}（{sign}{pct:.1f}%）",
                    size="sm",
                    weight="bold",
                    color=_pnl_color(total_pnl),
                    align="end",
                    flex=5,
                ),
            )
        )
    return {"type": "box", "layout": "vertical", "spacing": "sm", "paddingAll": "16px", "contents": rows}


def _industry_sections(items: list[dict]) -> list[dict]:
    """依產業別分組，各組加上產業標籤；「其他」排最後。"""
    groups: dict[str, list[dict]] = {}
    for item in items:
        groups.setdefault(item.get("industry") or FALLBACK_INDUSTRY, []).append(item)
    ordered = sorted(groups.items(), key=lambda kv: (kv[0] == FALLBACK_INDUSTRY, kv[0]))
    blocks = []
    for group_index, (industry, group_items) in enumerate(ordered):
        blocks.append(
            _text(industry, size="xs", weight="bold", color=INDUSTRY_COLOR, margin="lg" if group_index > 0 else "none")
        )
        for i, item in enumerate(group_items):
            if i > 0:
                blocks.append({"type": "separator"})
            blocks.append(_stock_block(item))
    return blocks


def build_chart_message(stock: dict, image_url: str, close: float | None, indicators: dict | None) -> dict:
    """K 線圖卡片：hero 放圖、body 放收盤價與技術指標摘要。"""
    title = f"{stock['stock_no']} {stock['name']}"
    market = f"・{stock['market']}" if stock.get("market") else ""
    body_rows = [
        _row(
            _text(f"{title}{market}", size="sm", weight="bold", color=TITLE_COLOR, flex=6, wrap=True),
            _text(format_number(close) if close is not None else "-", size="sm", align="end", color=TITLE_COLOR, flex=3),
        )
    ]
    if close is not None:
        body_rows += _indicator_rows({"indicators": indicators}, close)
    bubble = {
        "type": "bubble",
        "size": "giga",
        "hero": {"type": "image", "url": image_url, "size": "full", "aspectRatio": "16:13", "aspectMode": "fit"},
        "body": {"type": "box", "layout": "vertical", "spacing": "xs", "contents": body_rows},
    }
    return {"type": "flex", "altText": f"{title} K線圖", "contents": bubble}


def build_portfolio_message(member_name: str, entries: list[dict]) -> dict:
    """組出「我的股票」的 Flex Message；altText 沿用純文字版（通知列預覽用）。"""
    summary = summarize_portfolio(entries)
    blocks = _industry_sections(summary["items"])
    first_quote = next((item["quote"] for item in summary["items"] if item.get("quote")), None)
    bubble = {
        "type": "bubble",
        "size": "mega",
        "header": _header(member_name, format_roc_date(first_quote["date"]) if first_quote else None),
        "body": {"type": "box", "layout": "vertical", "spacing": "md", "contents": blocks},
        "styles": {"footer": {"separator": True}},
    }
    if summary["total_value"] > 0:
        bubble["footer"] = _footer(summary)
    return {
        "type": "flex",
        "altText": format_portfolio(member_name, entries)[:_ALT_TEXT_MAX],
        "contents": bubble,
    }
