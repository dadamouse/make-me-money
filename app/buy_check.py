"""買進/賣出檢查（買2330／賣2330）：把技術／籌碼／環境／訊號逐項攤開，每項附詳細白話。

原則同技術體檢：只陳列事實與解讀，不做預測、不給買賣建議。
"""
import logging
import re
import time
from html import unescape

from .deps import Deps
from .health import detailed_checks
from .parser import format_number, sign_of
from .premarket import fetch_quote
from .support_break import evaluate_signals

logger = logging.getLogger(__name__)

_VIX_SYMBOL = "^VIX"
_VIX_WARN = 25.0
_STREAK_WINDOW = 10  # 法人連續天數最多往回看的日數
_TITLE_COLOR = "#333333"
_MUTED_COLOR = "#777777"
_SECTION_COLOR = "#1A237E"

_ZCA_URL = "https://fubon-ebrokerdj.fbs.com.tw/z/zc/zca/zca_{stock_no}.djhtm"
_ZCA_HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
_PROFILE_TTL_SECONDS = 86400.0  # 營收比重一天抓一次就夠
_REVENUE_MIX_MAX = 180
_profile_cache: dict[str, tuple[float, str | None]] = {}
_ROW_PATTERN = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S | re.I)
_CELL_PATTERN = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.S | re.I)


def parse_revenue_mix(html_text: str) -> str | None:
    """MoneyDJ zca 基本資料頁 → 營收比重字串（公司靠什麼賺錢）。"""
    for row_html in _ROW_PATTERN.findall(html_text):
        cells = [unescape(re.sub(r"<[^>]+>", "", cell)).strip() for cell in _CELL_PATTERN.findall(row_html)]
        for i, cell in enumerate(cells):
            if cell == "營收比重" and i + 1 < len(cells) and cells[i + 1]:
                return cells[i + 1]
    return None


async def _revenue_mix(deps: Deps, stock_no: str) -> str | None:
    cached = _profile_cache.get(stock_no)
    if cached and time.monotonic() - cached[0] < _PROFILE_TTL_SECONDS:
        return cached[1]
    mix = None
    try:
        scraper = deps.scrape_http or deps.http  # fubon 憑證瑕疵，走 verify=False 通道
        response = await scraper.get(_ZCA_URL.format(stock_no=stock_no), headers=_ZCA_HEADERS, timeout=15)
        response.raise_for_status()
        response.encoding = "cp950"
        mix = parse_revenue_mix(response.text)
    except Exception:
        logger.warning("營收比重擷取失敗 stock_no=%s", stock_no, exc_info=True)
    _profile_cache[stock_no] = (time.monotonic(), mix)
    return mix


async def _company_profile_items(deps: Deps, stock: dict) -> list[dict]:
    """「這家公司做什麼」：產業別＋營收比重（事實陳列，不計入有利/風險計數）。"""
    items = []
    industry = stock.get("industry")
    market = f"・{stock['market']}" if stock.get("market") else ""
    if industry:
        items.append(_item(None, f"產業：{industry}{market}", ""))
    mix = await _revenue_mix(deps, stock["stock_no"])
    if mix:
        text = mix if len(mix) <= _REVENUE_MIX_MAX else mix[:_REVENUE_MIX_MAX] + "…"
        items.append(_item(None, f"營收比重：{text}", (
            "營收比重顯示公司靠什麼賺錢：佔比最高的產品線決定它跟哪個景氣循環連動，"
            "看新聞時可以對照「漲的理由」跟主要業務有沒有關係。"
        )))
    return items


def _item(ok: bool | None, text: str, why: str, action: dict | None = None) -> dict:
    """ok=True 有利、False 風險、None 中性參考；action 讓該行可點（如同業連鎖檢查）。"""
    return {"ok": ok, "text": text, "why": why, "action": action}


def _institutional_item(rows: list[dict]) -> dict | None:
    """rows：daily_institutional 新到舊。連續同向天數＋5 日累計。"""
    totals = []
    for row in rows:
        nets = [row.get(k) for k in ("foreign_net", "trust_net", "dealer_net")]
        if all(v is None for v in nets):
            continue
        totals.append(sum(float(v) for v in nets if v is not None))
    if not totals:
        return None
    positive = totals[0] > 0
    streak = 0
    for value in totals:
        if value != 0 and (value > 0) == positive:
            streak += 1
        else:
            break
    five_day = sum(totals[:5]) / 1000
    direction = "買超" if positive else "賣超"
    text = f"法人連 {streak} 日{direction}｜5 日累計 {sign_of(five_day)}{format_number(round(five_day))} 張"
    if positive:
        why = (
            "三大法人（外資、投信、自營商）是市場裡最大的資金：連續買超通常代表大資金看好、"
            "是波段行情的燃料；買超中斷轉賣的那天要提高警覺。"
        )
    else:
        why = (
            "三大法人連續賣超：大資金在退場，散戶接手的行情通常撐不久。"
            "想買至少等法人止賣（單日翻買超）再說。"
        )
    return _item(positive, ("✅ " if positive else "⚠️ ") + text, why)


def _broker_item(flow: dict | None) -> dict | None:
    """flow：daily_broker_flows 最新一筆。主力買賣超＋集中度。"""
    if not flow or flow.get("net_lots") is None:
        return None
    net = float(flow["net_lots"])
    conc = flow.get("concentration_pct")
    conc_text = f"｜集中度 {sign_of(float(conc))}{float(conc):.1f}%" if conc is not None else ""
    date_label = str(flow.get("trade_date", ""))[5:].replace("-", "/")
    positive = net > 0
    text = f"主力 {sign_of(net)}{format_number(round(net))} 張{conc_text}（{date_label}）"
    if positive:
        why = (
            "前 15 大券商分點的合計買賣超：主力買超且集中度為正，代表籌碼正從散戶流向大戶手中——"
            "大戶收貨的股票，籌碼相對安定。"
        )
    else:
        why = (
            "主力賣超（集中度為負）代表大戶在把籌碼倒給散戶：這種「籌碼發散」的階段，"
            "上漲缺乏大戶護盤，回檔時也容易跌得深。"
        )
    return _item(positive, ("✅ " if positive else "⚠️ ") + text, why)


def _margin_item(rows: list[dict]) -> dict | None:
    """rows：daily_margins 新到舊。融資 5 日增減。"""
    changes = [float(r["margin_change"]) for r in rows[:5] if r.get("margin_change") is not None]
    if not changes:
        return None
    five_day = sum(changes)
    text = f"融資 5 日 {sign_of(five_day)}{format_number(round(five_day))} 張"
    if five_day <= 0:
        why = (
            "融資是散戶借錢買的部位：近 5 日減少代表槓桿退場、籌碼沉澱——"
            "浮動籌碼變少，上漲時賣壓較輕，是偏正面的籌碼結構。"
        )
        return _item(True, "✅ " + text + "（槓桿退場、籌碼沉澱）", why)
    why = (
        "融資近 5 日增加：散戶正在借錢追價。融資部位是「不穩定的籌碼」，"
        "下跌時會被迫斷頭停損，把普通回檔放大成急殺——融資大增後追高要特別小心。"
    )
    return _item(False, "⚠️ " + text + "（散戶追價、籌碼變浮動）", why)


def _bollinger_item(indicators: dict) -> dict | None:
    pb = indicators.get("percent_b")
    if pb is None:
        return None
    if pb >= 0.9:
        return _item(False, f"⚠️ 布林 %b {pb:.2f}（貼近/衝出上軌）", (
            "%b 是價格在布林通道的位置（1＝上軌）：貼著上軌代表短線漲勢極強、但也極端——"
            "強勢股可以沿上軌走一段，然而「此刻進場」等於買在統計分布的頂端，回踩中軌（月線）是常態而非意外。"
        ))
    if pb <= 0.1:
        return _item(False, f"⚠️ 布林 %b {pb:.2f}（貼近/跌破下軌）", (
            "價格貼在布林下軌：短線超跌，但空頭中價格可以沿著下軌一路走，"
            "單獨這項不是買點，等站回下軌之上再視為超跌反彈訊號。"
        ))
    return _item(True, f"✅ 布林 %b {pb:.2f}（通道中段）", (
        "價格在布林通道中段：不追高也不接刀的位置，進出的統計風險相對平衡。"
    ))


async def _sector_items(deps: Deps, stock: dict, mode: str) -> list[dict]:
    """類股輪動：所屬類股近 5 日強弱排名（加減分）＋相關性同業（可點擊連鎖檢查）。"""
    industry = stock.get("industry")
    if not industry:
        return []
    items = []
    try:
        ranks = await deps.db.rpc("sector_momentum_rank", {"p_industry": industry})
        if ranks:
            row = ranks[0]
            rank, total, median = int(row["rank"]), int(row["total"]), float(row["median_pct"])
            fact = f"類股動能：{industry}近 5 日中位數 {sign_of(median)}{median:.1f}%（{total} 類中第 {rank} 名）"
            if rank <= max(total // 3, 1):
                items.append(_item(True, f"✅ {fact}", (
                    "台股有明顯的類股輪動：資金集中攻擊強勢族群時，同類股會互相拉抬——"
                    "買在強勢類股裡的個股，順風比逆風多，行情的持續性通常也較好。"
                )))
            elif rank > total * 2 // 3:
                items.append(_item(False, f"⚠️ {fact}", (
                    "所屬類股整體弱勢：資金不在這個族群時，個股題材再好也常獨木難撐；"
                    "想買可以等類股翻強（排名爬回前段）再動作，勝率較好。"
                )))
            else:
                items.append(_item(None, f"・{fact}", "類股強弱居中：族群資金流向不明顯，回歸個股自身條件判斷。"))
        peers = await deps.db.rpc("correlated_peers", {"p_stock_no": stock["stock_no"], "limit_n": 5})
        if peers:
            items.append(_item(None, "同業比較（近 5 日累計，點同業可直接檢查）", (
                "同業由股價連動性自動配對（近半年日報酬相關性最高的同分類公司，如友達↔群創、"
                "中興電↔華城士電），每日自動更新。同業齊漲是產業行情、持續性較好；"
                "只有自己動就要小心是單一題材。"
            )))
            command = "買" if mode == "buy" else "賣"
            for p in peers:
                label = f"› {p['stock_name']} {sign_of(float(p['pct']))}{float(p['pct']):.1f}%（連動 {float(p['correlation']):.2f}）"
                items.append(_item(None, label, "", action={
                    "type": "message", "label": str(p["stock_name"])[:20], "text": f"{command}{p['stock_no']}",
                }))
    except Exception:
        logger.warning("類股動能取得失敗 industry=%s", industry, exc_info=True)
    return items


async def _market_env_items(deps: Deps) -> list[dict]:
    """大盤位置與 VIX——個股再好，環境逆風時勝率整體變差。"""
    from .market_health import _index_series_with_realtime

    items = []
    try:
        series_desc, realtime = await _index_series_with_realtime(deps)
        closes = [float(r["taiex"]) for r in reversed(series_desc)]
        if len(closes) >= 20:
            ma20 = sum(closes[-20:]) / 20
            above = closes[-1] >= ma20
            bias = (closes[-1] - ma20) / ma20 * 100
            label = "即時" if realtime else "收盤"
            if above:
                items.append(_item(True, f"✅ 大盤在月線上方 {bias:+.1f}%（{label}）", (
                    "大盤是個股的順風或逆風：指數站在月線上，多數股票處於可做多的環境，"
                    "個股的多方訊號可信度較高。"
                )))
            else:
                items.append(_item(False, f"⚠️ 大盤在月線下方 {bias:+.1f}%（{label}）", (
                    "大盤收在月線之下：整體環境偏空，統計上多數個股跟著指數走——"
                    "此時就算個股訊號漂亮，也建議縮小部位、快進快出。"
                )))
    except Exception:
        logger.warning("買進檢查：大盤資料取得失敗", exc_info=True)
    try:
        vix = await fetch_quote(deps.http, _VIX_SYMBOL)
        if vix:
            hot = vix["price"] >= _VIX_WARN
            if hot:
                items.append(_item(False, f"⚠️ VIX {vix['price']:.1f}（恐慌區 ≥{_VIX_WARN:.0f}）", (
                    "VIX 是美股的恐慌指數：升破 25 代表國際避險情緒高漲，"
                    "外資容易縮手或賣超台股，消息面的風吹草動都會被放大。"
                )))
            else:
                items.append(_item(True, f"✅ VIX {vix['price']:.1f}（情緒平穩）", (
                    "國際恐慌指數在低檔：外部環境沒有明顯的避險壓力，市場情緒穩定。"
                )))
    except Exception:
        logger.warning("買進檢查：VIX 取得失敗", exc_info=True)
    return items


_SIGNAL_WHYS = {
    "B1": "站上 5 日線且 5 日線上彎，是該書定義的「積極買點」：代表短線最敏感的均線已翻多。",
    "B2": "帶量突破盤整區＋均線糾結＋中長紅，是勝率較高的「保守買點」：突破有量才算數。",
    "B3": "20 日內第二次突破（二次突破），代表第一次突破後的洗盤結束，是「加碼點」而非首次進場點。",
    "S1": "跌破 5 日線且 5 日線下彎，是該書的第一道出場警訊：強勢股不該跌破 5 日線。",
    "S2": "向下跳空缺口或中長黑代表賣方力道強，常是波段轉折的起點。",
    "S3": "跌破 10 日線＋KD 死亡交叉：第二道防線失守、動能翻空，波段結束機率大增。",
    "S4": "跌破「突破那根紅 K」的中間值：起漲紅 K 是多方成本區，跌破一半代表突破失敗（假突破）。",
    "S5": "跌破 20 日線且反彈無力：月線是最後一道防線，書中視為必須出場的訊號。",
}


def _signal_items(signals: dict | None, side: str) -> list[dict]:
    """支撐跌破法（楊忠憲）的買/賣條件：成立與否都列出來，附各自的意義。

    side="buy" 時成立算有利（進場點）；side="sell" 時成立算警訊（出場點）。
    """
    if not signals:
        return []
    items = []
    for sig in signals.get(side, []):
        mark = ("✅" if side == "buy" else "🚨") if sig["on"] else "・"
        status = "成立" if sig["on"] else "未成立"
        graded = (True if side == "buy" else False) if sig["on"] else None
        items.append(_item(
            graded,
            f"{mark} {sig['code']} {sig['label']}：{status}（{sig['text']}）",
            _SIGNAL_WHYS.get(sig["code"], ""),
        ))
    return items


def _verdict_lines(mode: str, favorable: int, risk: int, checks: list[dict], indicators: dict,
                   fired_sell: int = 0) -> list[str]:
    """依計數與情境給 1–2 句提醒（描述現況，不做預測）。"""
    overheated = any("過熱" in c["text"] or "正乖離過大" in c["text"] for c in checks) or (
        (indicators.get("percent_b") or 0) >= 0.9
    )
    trend_down = any("均線向下" in c["text"] for c in checks)
    if mode == "buy":
        lines = [f"→ 有利 {favorable} 項：風險 {risk} 項（計數僅供快速掃視，權重請自行判斷）"]
        if overheated and not trend_down:
            lines.append("・情境判讀：趨勢偏多但短線偏熱——「好股票、壞價位」，等回測月線或中軌通常有更好的成本。")
        elif trend_down:
            lines.append("・情境判讀：趨勢面偏空——逆勢接刀勝率低，等月線翻揚再找進場點比較穩。")
        else:
            lines.append("・情境判讀：多空訊號混雜——分批小部位試單，比一次全押穩健。")
    else:
        lines = [f"→ 續抱理由 {favorable} 項：出場警訊 {risk} 項（計數僅供快速掃視，權重請自行判斷）"]
        if fired_sell >= 2 or trend_down:
            lines.append("・情境判讀：多項出場警訊成立——分批減碼比一次全出容易執行，也遠比抱到破線凹單好。")
        elif overheated:
            lines.append("・情境判讀：結構未壞但短線過熱——可考慮先出一部分鎖住獲利，其餘設好停利點續抱。")
        else:
            lines.append("・情境判讀：續抱結構還在——與其預測高點，不如設好停利/停損線，跌破再執行。")
    lines.append("＊僅陳列事實供判讀，非投資建議")
    return lines


async def build_buy_check_message(
    deps: Deps, stock: dict, history: list[dict], image_url: str, indicators: dict, mode: str = "buy"
) -> dict:
    """組買進/賣出檢查 Flex：hero 放線圖，body 依分區列出每一項＋詳細說明。

    mode="buy"：有利／風險與注意＋買點訊號 B1–B3。
    mode="sell"：續抱理由／出場警訊＋賣出訊號 S1–S5（視角換成「該不該出場」）。
    """
    stock_no = stock["stock_no"]
    checks = detailed_checks(history)
    chips = []
    try:
        insti_rows = await deps.db.get(
            f"daily_institutional?stock_no=eq.{stock_no}&select=trade_date,foreign_net,trust_net,dealer_net"
            f"&order=trade_date.desc&limit={_STREAK_WINDOW}"
        )
        if item := _institutional_item(insti_rows):
            chips.append(item)
        flows = await deps.db.get(f"daily_broker_flows?stock_no=eq.{stock_no}&order=trade_date.desc&limit=1")
        if item := _broker_item(flows[0] if flows else None):
            chips.append(item)
        margins = await deps.db.get(f"daily_margins?stock_no=eq.{stock_no}&order=trade_date.desc&limit=5")
        if item := _margin_item(margins):
            chips.append(item)
    except Exception:
        logger.warning("買賣檢查：籌碼資料取得失敗 stock_no=%s", stock_no, exc_info=True)
    boll = _bollinger_item(indicators)
    sector = await _sector_items(deps, stock, mode)
    env = await _market_env_items(deps)
    side = "buy" if mode == "buy" else "sell"
    signals = _signal_items(evaluate_signals(history, stock.get("market")), side)
    fired_sell = sum(1 for s in signals if s["ok"] is False)

    graded_sector = [i for i in sector if i["ok"] is not None]  # 強/弱類股計入加減分
    neutral_sector = [i for i in sector if i["ok"] is None]      # 中性排名與同類股比價僅供參考
    graded = checks + chips + ([boll] if boll else []) + graded_sector + env
    favorable = [c for c in graded if c["ok"] is True]
    risks = [c for c in graded if c["ok"] is False]

    def _entry(item: dict) -> list[dict]:
        row = {"type": "text", "text": item["text"], "size": "xs", "color": _TITLE_COLOR, "wrap": True,
               "margin": "md", "weight": "bold"}
        if item.get("action"):
            row["action"] = item["action"]
            row["color"] = "#1565C0"  # 可點的行用藍色提示
        rows = [row]
        if item.get("why"):
            rows.append({"type": "text", "text": item["why"], "size": "xxs", "color": _MUTED_COLOR, "wrap": True})
        return rows

    def _section(title: str, items: list[dict]) -> list[dict]:
        if not items:
            return []
        header = [{"type": "separator", "margin": "lg"},
                  {"type": "text", "text": title, "size": "sm", "weight": "bold", "color": _SECTION_COLOR, "margin": "lg"}]
        return header + [row for item in items for row in _entry(item)]

    close = history[-1]["close"]
    if mode == "buy":
        title = f"🛒 買進檢查 {stock_no} {stock['name']}"
        section_titles = (f"✅ 有利（{len(favorable)}）", f"⚠️ 風險與注意（{len(risks)}）", "🚨 買點訊號（支撐跌破法）")
    else:
        title = f"📤 賣出檢查 {stock_no} {stock['name']}"
        section_titles = (f"✅ 續抱理由（{len(favorable)}）", f"⚠️ 出場警訊（{len(risks)}）", "🚨 賣出訊號（支撐跌破法）")
    body = [
        {"type": "text", "text": title, "size": "md", "weight": "bold", "color": _TITLE_COLOR},
        {"type": "text", "text": f"現價 {format_number(close)}", "size": "sm", "color": _MUTED_COLOR},
    ]
    body += _section("🏢 這家公司做什麼", await _company_profile_items(deps, stock))
    body += _section(section_titles[0], favorable)
    body += _section(section_titles[1], risks)
    body += _section("🔄 類股參考", neutral_sector)
    body += _section(section_titles[2], signals)
    body.append({"type": "separator", "margin": "lg"})
    for line in _verdict_lines(mode, len(favorable), len(risks), graded, indicators, fired_sell):
        body.append({"type": "text", "text": line, "size": "xs", "color": _TITLE_COLOR, "wrap": True, "margin": "md"})

    alt_lines = [f"{title}（{format_number(close)}）"]
    alt_lines += [c["text"] for c in favorable + risks]
    bubble = {
        "type": "bubble",
        "size": "giga",
        "hero": {"type": "image", "url": image_url, "size": "full", "aspectRatio": "13:10", "aspectMode": "fit"},
        "body": {"type": "box", "layout": "vertical", "spacing": "xs", "contents": body},
        "footer": {
            "type": "box",
            "layout": "horizontal",
            "contents": [{
                "type": "button", "style": "primary", "height": "sm", "color": "#455A64",
                "action": {"type": "message", "label": "📰 相關新聞", "text": f"新聞{stock_no}"},
            }],
        },
    }
    return {"type": "flex", "altText": "\n".join(alt_lines)[:400], "contents": bubble}
