"""純函式：指令解析、TWSE 回應解析、持股彙總與訊息格式化。"""
import re
from dataclasses import dataclass

HELP_TEXT = "\n".join(
    [
        "📖 功能選單（直接回覆數字）",
        "1️⃣ 簡易持股｜總覽卡：損益、法人、資券、指標",
        "2️⃣ 詳細持股｜每檔持股一張技術分析圖",
        "3️⃣ 今日資訊｜持股重大訊息與除權息提醒",
        "4️⃣ 量增排行｜全市場今日量增前 10 名",
        "5️⃣ 每日選股｜八策略選股卡片（附網頁版）",
        "6️⃣ 大盤體檢｜指數、量能、法人、融資、寬度、VIX",
        "",
        "📝 常用指令（照著打即可）",
        "▸ 登入你的名字｜第一次使用",
        "▸ 切換家人名字｜幫家人記帳",
        "▸ +2330 1000 850｜新增持股",
        "　（代號 股數 成本，後兩項可省略）",
        "▸ -2330｜刪除持股",
        "▸ 圖2330｜技術分析圖",
        "▸ 買2330｜買進前總檢查（附圖＋逐項解說）",
        "▸ 訊號2330｜支撐跌破法買賣訊號",
        "▸ 清空持股｜需再回覆「確認」",
        "",
        "💡 小技巧",
        "・建檔最快：多行一次貼上，每行一筆「+代號 股數 成本」",
        "・名稱、代號都能用：+台積電、圖捷敏ky 都通",
        "・「新增/刪除」與「+/-」通用",
    ]
)

# 無待選項目時，數字直接對應功能選單
MENU_ACTIONS = {1: "list", 2: "charts_all", 3: "news", 4: "volume_rank", 5: "picks", 6: "health"}

_NUMBER = r"(\d+(?:\.\d+)?)"


@dataclass(frozen=True)
class Command:
    action: str
    name: str | None = None
    stock: str | None = None
    shares: float | None = None
    cost: float | None = None
    index: int | None = None


def parse_command(raw_text: str | None) -> Command:
    text = str(raw_text or "").strip()
    if m := re.fullmatch(r"[1-6]", text):
        return Command(action="pick", index=int(m.group(0)))
    if m := re.fullmatch(r"登入\s*(\S+)", text):
        return Command(action="login", name=m.group(1))
    if m := re.fullmatch(r"切換\s*(\S+)", text):
        return Command(action="switch", name=m.group(1))
    if m := re.fullmatch(rf"(?:新增|[+＋])\s*(\S+?)(?:\s+{_NUMBER})?(?:\s+{_NUMBER})?", text):
        return Command(
            action="add",
            stock=m.group(1),
            shares=None if m.group(2) is None else float(m.group(2)),
            cost=None if m.group(3) is None else float(m.group(3)),
        )
    if m := re.fullmatch(r"(?:刪除|[-－])\s*(\S+)", text):
        return Command(action="remove", stock=m.group(1))
    if re.fullmatch(r"我的股票|簡易持股|清單|列表", text):
        return Command(action="list")
    if re.fullmatch(r"今日資訊|重大訊息|新聞", text):
        return Command(action="news")
    if re.fullmatch(r"詳細持股|持股線圖|持股圖|我的線圖", text):
        return Command(action="charts_all")
    if m := re.fullmatch(r"(?:線圖|[Kk]線|圖)\s*(\S+)", text):
        return Command(action="chart", stock=m.group(1))
    if m := re.fullmatch(r"(?:訊號|信號)\s*(\S+)", text):
        return Command(action="signal", stock=m.group(1))
    if m := re.fullmatch(r"買\s*(\S+)", text):
        return Command(action="buy_check", stock=m.group(1))
    if re.fullmatch(r"量增排行|量增", text):
        return Command(action="volume_rank")
    if re.fullmatch(r"每日選股|選股", text):
        return Command(action="picks")
    if re.fullmatch(r"大盤體檢|體檢|大盤", text):
        return Command(action="health")
    if re.fullmatch(r"清空持股|全部刪除|清空全部", text):
        return Command(action="clear")
    if re.fullmatch(r"確認|確認清空", text):
        return Command(action="confirm")
    return Command(action="help")


def parse_twse_close(api_json: dict | None) -> dict | None:
    """TWSE STOCK_DAY 回應 → 最新收盤價。

    data 每列：[日期(民國), 成交股數, 成交金額, 開盤, 最高, 最低, 收盤, 漲跌, 筆數]
    """
    if not api_json or api_json.get("stat") != "OK":
        return None
    rows = api_json.get("data")
    if not isinstance(rows, list) or not rows:
        return None
    for row in reversed(rows):
        try:
            close = float(str(row[6]).replace(",", ""))
        except (ValueError, IndexError):
            continue
        return {"date": str(row[0]), "close": close}
    return None


def format_roc_date(roc_date: str | None) -> str:
    """民國日期 '115/07/02' 或 ISO '2026-07-02' → 顯示用 '07/02'。"""
    s = str(roc_date or "")
    separator = "-" if "-" in s else "/"
    parts = s.split(separator)
    return f"{parts[1]}/{parts[2]}" if len(parts) == 3 else s


def format_roc_compact(compact: str | None) -> str:
    """TPEx 緊湊民國日期 '1150703' → '115/07/03'。"""
    s = str(compact or "")
    return f"{s[:-4]}/{s[-4:-2]}/{s[-2:]}" if len(s) >= 7 else s


def roc_compact_to_iso(compact: str | None) -> str | None:
    """緊湊民國日期 '1150702' → ISO '2026-07-02'。"""
    s = str(compact or "").strip()
    if len(s) < 7 or not s.isdigit():
        return None
    return f"{int(s[:-4]) + 1911}-{s[-4:-2]}-{s[-2:]}"


def roc_slash_to_iso(roc_date: str | None) -> str | None:
    """斜線民國日期 '115/07/01' → ISO '2026-07-01'。"""
    parts = str(roc_date or "").strip().split("/")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        return None
    return f"{int(parts[0]) + 1911}-{parts[1]:0>2}-{parts[2]:0>2}"


def parse_tpex_close(quotes: list | None, stock_no: str) -> dict | None:
    """TPEx 每日收盤行情（全市場清單）→ 指定代號的收盤價。"""
    for quote in quotes or []:
        if str(quote.get("SecuritiesCompanyCode")) != str(stock_no):
            continue
        try:
            close = float(str(quote.get("Close", "")).replace(",", ""))
        except ValueError:
            return None
        return {"date": format_roc_compact(quote.get("Date")), "close": close}
    return None


def aggregate_holdings(rows: list[dict]) -> list[dict]:
    """holdings 資料列（stock_no, shares, cost_price）→ 依代號彙總。"""
    by_stock: dict[str, dict] = {}
    for row in rows or []:
        stock_no = str(row["stock_no"])
        prev = by_stock.get(stock_no, {"stock_no": stock_no, "shares": 0.0, "cost": 0.0})
        shares = float(row.get("shares") or 0)
        cost_price = row.get("cost_price")
        added_cost = shares * float(cost_price) if cost_price is not None and shares > 0 else 0.0
        by_stock[stock_no] = {
            "stock_no": stock_no,
            "shares": prev["shares"] + shares,
            "cost": prev["cost"] + added_cost,
        }
    return list(by_stock.values())


def format_number(n: float) -> str:
    """千分位、最多兩位小數、去除尾端零，如 2355000 → '2,355,000'、850.5 → '850.5'。"""
    return f"{float(n):,.2f}".rstrip("0").rstrip(".")


def summarize_portfolio(entries: list[dict]) -> dict:
    """entries: [{stock_no, name, shares, cost, quote: {date, close} | None}]
    → 每檔補上 value/pnl/pct，並計算總市值、總成本、總損益（供文字版與 Flex 版共用）。
    """
    items = []
    total_value = 0.0
    total_cost = 0.0
    costed_value = 0.0  # 只累計有成本的持股市值：總損益不能被「沒記成本」的持股灌水
    for entry in entries:
        item = {**entry, "value": None, "pnl": None, "pct": None}
        quote = entry.get("quote")
        if quote and entry["shares"] > 0:
            item["value"] = entry["shares"] * quote["close"]
            total_value += item["value"]
            if entry["cost"] > 0:
                total_cost += entry["cost"]
                costed_value += item["value"]
                item["pnl"] = item["value"] - entry["cost"]
                item["pct"] = item["pnl"] / entry["cost"] * 100
        items.append(item)
    total_pnl = costed_value - total_cost if total_cost > 0 else None
    return {"items": items, "total_value": total_value, "total_cost": total_cost, "total_pnl": total_pnl}


def sign_of(value: float) -> str:
    return "+" if value >= 0 else ""


def format_portfolio(member_name: str, entries: list[dict]) -> str:
    summary = summarize_portfolio(entries)
    lines = [f"📊 {member_name} 的持股"]
    for item in summary["items"]:
        quote = item.get("quote")
        if not quote:
            lines.append(f"{item['stock_no']} {item['name']}　⚠️ 查無報價（可能為上櫃或停牌）")
            continue
        lines.append(
            f"{item['stock_no']} {item['name']}　收盤 {format_number(quote['close'])}（{format_roc_date(quote['date'])}）"
        )
        if item["shares"] > 0:
            pnl_text = ""
            if item["pnl"] is not None:
                sign = sign_of(item["pnl"])
                pnl_text = f"｜損益 {sign}{format_number(item['pnl'])}（{sign}{item['pct']:.1f}%）"
            lines.append(f"　{format_number(item['shares'])} 股｜市值 {format_number(item['value'])}{pnl_text}")
        else:
            lines.append("　觀察中（未記股數）")
    if summary["total_value"] > 0:
        lines.append("─────────")
        total_line = f"總市值 {format_number(summary['total_value'])}"
        if summary["total_pnl"] is not None:
            total_line += f"｜總損益 {sign_of(summary['total_pnl'])}{format_number(summary['total_pnl'])}"
        lines.append(total_line)
    return "\n".join(lines)
