"""網頁版持股 dashboard：簽章驗證與 HTML 渲染。"""
import hashlib
import hmac
from html import escape

from .parser import format_number, format_roc_date, sign_of


def portfolio_sig(member_id: int, sign_key: str) -> str:
    digest = hmac.new(sign_key.encode(), f"portfolio:{member_id}".encode(), hashlib.sha256).hexdigest()
    return digest[:16]


def verify_portfolio_sig(member_id: int, sign_key: str, provided: str | None) -> bool:
    return bool(provided) and hmac.compare_digest(portfolio_sig(member_id, sign_key), provided)


def picks_sig(sign_key: str) -> str:
    return hmac.new(sign_key.encode(), b"daily-picks", hashlib.sha256).hexdigest()[:16]


def verify_picks_sig(sign_key: str, provided: str | None) -> bool:
    return bool(provided) and hmac.compare_digest(picks_sig(sign_key), provided)


_CSS = """
body{font-family:-apple-system,'Noto Sans TC',sans-serif;background:#f2f4f8;margin:0;padding:16px;color:#333}
h1{font-size:20px;margin:8px 4px 16px}
.summary{background:#27346A;color:#fff;border-radius:12px;padding:16px;margin-bottom:16px}
.summary .total{font-size:24px;font-weight:700}
.card{background:#fff;border-radius:12px;padding:16px;margin-bottom:16px;box-shadow:0 1px 3px rgba(0,0,0,.08)}
.card h2{font-size:17px;margin:0 0 4px}
.tag{font-size:12px;color:#888;font-weight:400;margin-left:6px}
.price{font-size:22px;font-weight:700;margin:4px 0}
.up{color:#E53935}.down{color:#43A047}.muted{color:#9E9E9E;font-size:13px}
table{width:100%;border-collapse:collapse;font-size:13px;margin:8px 0}
td{padding:4px 2px;border-bottom:1px solid #f0f0f0}
td:last-child{text-align:right}
img.chart{width:100%;border-radius:8px;margin-top:8px}
.pnl{font-size:14px;font-weight:600}
footer{text-align:center;color:#aaa;font-size:12px;margin:24px 0}
"""


def _pnl_class(value: float) -> str:
    return "up" if value >= 0 else "down"


def _row(label: str, value: str) -> str:
    return f"<tr><td class='muted'>{escape(label)}</td><td>{value}</td></tr>"


def _signed(value: float) -> str:
    rounded = round(value)
    return f"{'+' if rounded >= 0 else ''}{format_number(rounded)}"


def _stock_card(entry: dict) -> str:
    quote = entry.get("quote")
    name = escape(f"{entry['stock_no']} {entry['name']}")
    tags = "・".join(t for t in (entry.get("market"), entry.get("industry")) if t)
    price_html = "<div class='muted'>查無報價</div>"
    rows = []
    if quote:
        pnl_html = ""
        if entry.get("value") is not None:
            rows.append(_row("持股", f"{format_number(entry['shares'])} 股"))
            rows.append(_row("市值", format_number(entry["value"])))
            if entry.get("pnl") is not None:
                pnl = entry["pnl"]
                pnl_html = (
                    f"<span class='pnl {_pnl_class(pnl)}'>"
                    f"{sign_of(pnl)}{format_number(pnl)}（{sign_of(pnl)}{entry['pct']:.1f}%）</span>"
                )
        price_html = (
            f"<div class='price'>{format_number(quote['close'])}"
            f"<span class='muted'>（{escape(format_roc_date(quote['date']))}）</span> {pnl_html}</div>"
        )
    indicators = entry.get("indicators") or {}
    ma_parts = [
        f"MA{n} {format_number(v)}"
        for n, v in ((5, indicators.get("ma5")), (20, indicators.get("ma20")), (60, indicators.get("ma60")))
        if v is not None
    ]
    if ma_parts:
        rows.append(_row("均線", "｜".join(ma_parts)))
    osc_parts = []
    if indicators.get("rsi14") is not None:
        osc_parts.append(f"RSI {indicators['rsi14']:.0f}")
    if indicators.get("k") is not None:
        osc_parts.append(f"K {indicators['k']:.0f}／D {indicators['d']:.0f}／J {indicators['j']:.0f}")
    if osc_parts:
        rows.append(_row("指標", "｜".join(osc_parts)))
    institutional = entry.get("institutional")
    if institutional and institutional.get("total_net") is not None:
        total = float(institutional["total_net"])
        detail = "｜".join(
            f"{label}{_signed(float(v) / 1000)}"
            for label, v in (
                ("外資", institutional.get("foreign_net")),
                ("投信", institutional.get("trust_net")),
                ("自營", institutional.get("dealer_net")),
            )
            if v is not None
        )
        rows.append(_row("法人", f"<span class='{_pnl_class(total)}'>{_signed(total / 1000)} 張</span>（{detail}）"))
    margin = entry.get("margin")
    if margin and margin.get("margin_balance") is not None:
        text = f"融資 {format_number(margin['margin_balance'])} 張"
        if margin.get("margin_change") is not None:
            text += f"（{_signed(margin['margin_change'])}）"
        if margin.get("short_balance") is not None:
            text += f"｜融券 {format_number(margin['short_balance'])} 張"
        rows.append(_row("資券", text))
    return (
        f"<div class='card'><h2>{name}<span class='tag'>{escape(tags)}</span></h2>"
        f"{price_html}<table>{''.join(rows)}</table>"
        f"{_chart_img(entry['stock_no'])}</div>"
    )


_CHART_FALLBACK = (
    "onerror=\"this.outerHTML='<div class=&quot;muted&quot; style=&quot;padding:12px&quot;>"
    "⏳ 歷史資料回補中，圖表稍後才會出現（背景任務進行中）</div>'\""
)


def _chart_img(stock_no: str) -> str:
    return (
        f"<img class='chart' loading='lazy' src='/stock-chart/{escape(stock_no)}.png' "
        f"alt='技術分析圖' {_CHART_FALLBACK}>"
    )


def _pick_card(pick: dict) -> str:
    return (
        f"<div class='card'><h2>{escape(pick['stock_no'])} {escape(pick['name'])}</h2>"
        f"<div class='muted'>{escape(pick['detail'])}</div>"
        f"{_chart_img(pick['stock_no'])}</div>"
    )


def render_picks_html(result: dict) -> str:
    """每日選股網頁版：每個策略一節，入選個股附技術分析圖。"""
    sections_html = []
    for section in result["sections"]:
        parts = [f"<div class='summary'><div class='total'>🎯 {escape(section['title'])}</div>"]
        parts.append(f"<div style='color:#B8C4E0;font-size:13px'>{escape(section['desc'])}</div></div>")
        if section["skipped"]:
            parts.append(f"<div class='card muted'>⏳ {escape(section['skipped'])}</div>")
        else:
            for group in section["markets"]:
                parts.append(f"<h1>▍{escape(group['market'])}</h1>")
                if group["picks"]:
                    parts += [_pick_card(pick) for pick in group["picks"]]
                else:
                    parts.append("<div class='card muted'>無符合標的</div>")
        sections_html.append("".join(parts))
    return f"""<!doctype html>
<html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex">
<title>每日選股 {escape(result['date'])}</title><style>{_CSS}</style></head>
<body>
<h1>🎯 每日選股（{escape(result['date'])}）</h1>
{''.join(sections_html)}
<footer>僅供參考，非投資建議・make-me-money</footer>
</body></html>"""


def render_portfolio_html(member_name: str, entries: list[dict], total_value: float, total_pnl: float | None) -> str:
    pnl_html = ""
    if total_pnl is not None:
        pnl_html = f"<div class='pnl {_pnl_class(total_pnl)}'>總損益 {sign_of(total_pnl)}{format_number(total_pnl)}</div>"
    cards = "".join(_stock_card(entry) for entry in entries)
    return f"""<!doctype html>
<html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex">
<title>{escape(member_name)} 的持股</title><style>{_CSS}</style></head>
<body>
<h1>📊 {escape(member_name)} 的持股</h1>
<div class="summary"><div class="muted" style="color:#B8C4E0">總市值</div>
<div class="total">{format_number(total_value)}</div>{pnl_html}</div>
{cards}
<footer>make-me-money・資料為最近交易日收盤</footer>
</body></html>"""
