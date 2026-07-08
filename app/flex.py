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
                size="md" if quote else "sm",
                weight="bold",
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
                _row(
                    _text("損益", size="xs", color=MUTED_COLOR, flex=2),
                    _text(
                        f"{sign}{format_number(item['pnl'])}（{sign}{item['pct']:.1f}%）",
                        size="sm",
                        weight="bold",
                        align="end",
                        color=_pnl_color(item["pnl"]),
                        flex=6,
                    ),
                )
            )
    else:
        rows.append(_text(f"{market_prefix}觀察中（未記股數）", size="xs", color=MUTED_COLOR))
    rows += _indicator_rows(item, quote["close"])
    rows += _flow_rows(item)
    return {
        "type": "box",
        "layout": "vertical",
        "spacing": "xs",
        "paddingTop": "6px",
        "paddingBottom": "6px",
        # 點整個持股區塊 → 自動送出「圖XXXX」看技術分析圖
        "action": {"type": "message", "label": item["stock_no"], "text": f"圖{item['stock_no']}"},
        "contents": rows,
    }


def _signed(value: float) -> str:
    rounded = round(value)
    return f"{'+' if rounded >= 0 else ''}{format_number(rounded)}"


def _signed_pct(value: float) -> str:
    return f"{'+' if value >= 0 else ''}{value:.1f}%"


def _signed_lots(shares_value: float) -> str:
    """買賣超股數 → 帶正負號的張數。"""
    return _signed(shares_value / 1000)


def _flow_rows(item: dict) -> list[dict]:
    """三大法人與融資融券濃縮成一行（法人合計依買賣超上色）。"""
    spans = []
    institutional = item.get("institutional")
    if institutional and institutional.get("total_net") is not None:
        total = institutional["total_net"]
        detail = "｜".join(
            f"{label}{_signed_lots(value)}"
            for label, value in (
                ("外資", institutional.get("foreign_net")),
                ("投信", institutional.get("trust_net")),
                ("自營", institutional.get("dealer_net")),
            )
            if value is not None
        )
        spans.append({"type": "span", "text": f"法人 {_signed_lots(total)} 張", "color": _pnl_color(total), "weight": "bold"})
        if detail:
            spans.append({"type": "span", "text": f"（{detail}）", "color": MUTED_COLOR})
    broker_flow = item.get("broker_flow")
    if broker_flow and broker_flow.get("net_lots") is not None:
        net = float(broker_flow["net_lots"])
        text = f"主力 {_signed(net)} 張"
        if broker_flow.get("concentration_pct") is not None:
            text += f"（集中 {_signed_pct(float(broker_flow['concentration_pct']))}）"
        spans.append({"type": "span", "text": ("　" if spans else "") + text, "color": _pnl_color(net), "weight": "bold"})
    margin = item.get("margin")
    if margin and margin.get("margin_balance") is not None:
        text = f"融資 {format_number(margin['margin_balance'])} 張"
        if margin.get("margin_change") is not None:
            text += f"（{_signed(margin['margin_change'])}）"
        if margin.get("short_balance") is not None:
            text += f"｜融券 {format_number(margin['short_balance'])} 張"
            if margin.get("short_change") is not None:
                text += f"（{_signed(margin['short_change'])}）"
        spans.append({"type": "span", "text": ("　" if spans else "") + text, "color": MUTED_COLOR})
    if not spans:
        return []
    return [{"type": "text", "size": "xxs", "wrap": True, "contents": spans}]


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


def _header(member_name: str, date_label: str | None, summary: dict | None = None) -> dict:
    """漸層色標題區：名稱＋總市值（大字）＋總損益（上色）＋收盤日。"""
    contents = [_text(f"📊 {member_name} 的持股", size="md", weight="bold", color="#FFFFFF")]
    if summary and summary["total_value"] > 0:
        contents.append(
            _text(f"總市值 {format_number(summary['total_value'])}", size="xxl", weight="bold", color="#FFFFFF", margin="sm")
        )
        if summary["total_pnl"] is not None and summary["total_cost"] > 0:
            pnl = summary["total_pnl"]
            pct = pnl / summary["total_cost"] * 100
            contents.append(
                _text(
                    f"總損益 {sign_of(pnl)}{format_number(pnl)}（{sign_of(pnl)}{pct:.1f}%）",
                    size="sm",
                    weight="bold",
                    color="#FF8A80" if pnl >= 0 else "#B9F6CA",  # 深底上的亮紅/亮綠
                )
            )
    if date_label:
        contents.append(_text(f"收盤日 {date_label}", size="xs", color=HEADER_SUB_COLOR, margin="sm"))
    return {
        "type": "box",
        "layout": "vertical",
        "paddingAll": "20px",
        "background": {"type": "linearGradient", "angle": "135deg", "startColor": "#1A237E", "endColor": "#3949AB"},
        "contents": contents,
    }


def _link_button(url: str, label: str) -> dict:
    return {"type": "button", "style": "link", "height": "sm", "action": {"type": "uri", "label": label, "uri": url}}


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


def build_chart_bubble(
    stock: dict,
    image_url: str,
    close: float | None,
    indicators: dict | None,
    size: str = "giga",
    page_url: str | None = None,
    extra_line: str | None = None,
) -> dict:
    """K 線圖卡片 bubble：hero 放圖、body 放收盤價與技術指標摘要。"""
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
    if extra_line:
        body_rows.append(_text(extra_line, size="xxs", color=MUTED_COLOR, wrap=True))
    bubble = {
        "type": "bubble",
        "size": size,
        "hero": {"type": "image", "url": image_url, "size": "full", "aspectRatio": "16:13", "aspectMode": "fit"},
        "body": {"type": "box", "layout": "vertical", "spacing": "xs", "contents": body_rows},
    }
    if page_url:
        bubble["footer"] = {
            "type": "box",
            "layout": "vertical",
            "contents": [_link_button(page_url, "🔗 開啟網頁版")],
        }
    return bubble


def build_chart_message(
    stock: dict,
    image_url: str,
    close: float | None,
    indicators: dict | None,
    page_url: str | None = None,
    extra_line: str | None = None,
) -> dict:
    title = f"{stock['stock_no']} {stock['name']}"
    return {
        "type": "flex",
        "altText": f"{title} K線圖",
        "contents": build_chart_bubble(stock, image_url, close, indicators, page_url=page_url, extra_line=extra_line),
    }


_PICK_HEADER_COLORS = ("#27346A", "#00695C", "#B26A00", "#6A1B9A", "#AD1457", "#37474F")


_PICKS_CARD_MAX = 5  # 卡片顯示前 N 檔（其餘見網頁版，避免超過 LINE 訊息大小上限）


def _picks_group_rows(group: dict) -> list[dict]:
    rows = [_text(f"▍{group['market']}", size="xs", weight="bold", color=INDUSTRY_COLOR, margin="md")]
    if not group["picks"]:
        rows.append(_text("無符合標的", size="xs", color=MUTED_COLOR))
        return rows
    for pick in group["picks"][:_PICKS_CARD_MAX]:
        rows.append(
            {
                "type": "box",
                "layout": "vertical",
                "spacing": "none",
                "margin": "sm",
                "action": {"type": "message", "label": pick["stock_no"], "text": f"圖{pick['stock_no']}"},
                "contents": [
                    _text(f"{pick['stock_no']} {pick['name']}", size="sm", weight="bold", color=TITLE_COLOR),
                    _text(pick["detail"], size="xxs", color=MUTED_COLOR, wrap=True),
                ],
            }
        )
    hidden = len(group["picks"]) - _PICKS_CARD_MAX
    if hidden > 0:
        rows.append(_text(f"…還有 {hidden} 檔，開網頁版看完整前 10 名", size="xxs", color=MUTED_COLOR))
    return rows


def _picks_strategy_bubble(section: dict, index: int, date_label: str, web_url: str | None) -> dict:
    header_color = _PICK_HEADER_COLORS[index % len(_PICK_HEADER_COLORS)]
    header = {
        "type": "box",
        "layout": "vertical",
        "backgroundColor": header_color,
        "paddingAll": "14px",
        "contents": [
            _text(f"🎯 {section['title']}", size="md", weight="bold", color="#FFFFFF"),
            _text(f"{date_label}｜{section['desc']}", size="xxs", color="#D5DBEA", wrap=True, margin="xs"),
        ],
    }
    if section["skipped"]:
        body_rows = [_text(f"⏳ {section['skipped']}", size="xs", color=MUTED_COLOR, wrap=True)]
    else:
        body_rows = []
        for group in section["markets"]:
            body_rows += _picks_group_rows(group)
        body_rows.append(_text("點個股可看技術分析圖", size="xxs", color="#BDBDBD", margin="lg"))
    bubble = {
        "type": "bubble",
        "size": "mega",
        "header": header,
        "body": {"type": "box", "layout": "vertical", "spacing": "xs", "contents": body_rows},
    }
    if web_url:
        bubble["footer"] = {
            "type": "box",
            "layout": "vertical",
            "contents": [_link_button(web_url, "🔗 網頁版完整檢視（含線圖）")],
        }
    return bubble


def build_picks_message(result: dict, alt_text: str, web_url: str | None = None) -> dict:
    """每日選股 Flex：每個策略一張卡片的 carousel，個股點擊可看線圖。"""
    date_label = result["date"][5:].replace("-", "/")
    bubbles = [
        _picks_strategy_bubble(section, i, date_label, web_url) for i, section in enumerate(result["sections"])
    ]
    return {"type": "flex", "altText": alt_text[:400], "contents": {"type": "carousel", "contents": bubbles}}


def build_chart_carousel_message(member_name: str, bubbles: list[dict]) -> dict:
    """多檔持股線圖 carousel（LINE 上限 12 個 bubble；carousel 內不可用 giga）。"""
    return {
        "type": "flex",
        "altText": f"{member_name} 的持股線圖（{len(bubbles)} 檔）",
        "contents": {"type": "carousel", "contents": bubbles},
    }


def build_portfolio_message(member_name: str, entries: list[dict], portfolio_link: str | None = None) -> dict:
    """組出「我的股票」的 Flex Message；altText 沿用純文字版（通知列預覽用）。"""
    summary = summarize_portfolio(entries)
    blocks = _industry_sections(summary["items"])
    first_quote = next((item["quote"] for item in summary["items"] if item.get("quote")), None)
    bubble = {
        "type": "bubble",
        "size": "mega",
        "header": _header(member_name, format_roc_date(first_quote["date"]) if first_quote else None, summary),
        "body": {"type": "box", "layout": "vertical", "spacing": "md", "contents": blocks},
        "styles": {"footer": {"separator": True}},
    }
    if portfolio_link:
        bubble["footer"] = {
            "type": "box",
            "layout": "vertical",
            "contents": [_link_button(portfolio_link, "🔗 開啟網頁版")],
        }
    return {
        "type": "flex",
        "altText": format_portfolio(member_name, entries)[:_ALT_TEXT_MAX],
        "contents": bubble,
    }
