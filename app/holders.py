"""集保股權分散（TDCC 週資料）：同步與籌碼集中度摘要。"""
import logging

from .deps import Deps
from .parser import format_number, sign_of

logger = logging.getLogger(__name__)

TDCC_URL = "https://opendata.tdcc.com.tw/getOD.ashx?id=1-5"
_LEVEL_BIG = "15"    # 1,000,001 股以上（千張大戶）
_LEVEL_TOTAL = "17"  # 合計
_CHUNK_SIZE = 500


def parse_holders_csv(csv_text: str) -> list[dict]:
    """TDCC CSV → weekly_holders 資料列。欄位：資料日期,證券代號,持股分級,人數,股數,占比%"""
    by_stock: dict[str, dict] = {}
    for line in csv_text.splitlines()[1:]:
        parts = line.split(",")
        if len(parts) < 6:
            continue
        date_raw, code, level = parts[0].strip(), parts[1].strip(), parts[2].strip()
        if len(date_raw) != 8 or not date_raw.isdigit():
            continue
        week_date = f"{date_raw[:4]}-{date_raw[4:6]}-{date_raw[6:8]}"
        row = by_stock.setdefault(code, {"stock_no": code, "week_date": week_date})
        try:
            if level == _LEVEL_BIG:
                row["big_holders"] = float(parts[3])
                row["big_ratio"] = float(parts[5])
            elif level == _LEVEL_TOTAL:
                row["total_holders"] = float(parts[3])
        except ValueError:
            continue
    return [row for row in by_stock.values() if "total_holders" in row]


async def sync_holders(deps: Deps) -> dict:
    """抓最新一週集保資料並 upsert（每週更新，重跑冪等）。"""
    response = await deps.http.get(TDCC_URL, timeout=120)
    response.raise_for_status()
    rows = parse_holders_csv(response.text)
    if not rows:
        raise RuntimeError("TDCC 回傳空資料")
    for i in range(0, len(rows), _CHUNK_SIZE):
        await deps.db.insert(
            "weekly_holders?on_conflict=stock_no,week_date",
            rows[i : i + _CHUNK_SIZE],
            prefer="resolution=merge-duplicates",
        )
    return {"rows": len(rows), "week": rows[0]["week_date"]}


async def holders_summary_line(deps: Deps, stock_no: str) -> str | None:
    """個股籌碼集中度摘要：千張大戶比與股東數的週變化。"""
    rows = await deps.db.get(
        f"weekly_holders?stock_no=eq.{stock_no}&order=week_date.desc&limit=2"
    )
    if not rows:
        return None
    current = rows[0]
    if current.get("big_ratio") is None:
        return None
    text = f"千張大戶 {float(current['big_ratio']):.1f}%"
    holders_text = f"股東 {format_number(float(current['total_holders']))} 人"
    if len(rows) == 2 and rows[1].get("big_ratio") is not None:
        previous = rows[1]
        ratio_delta = float(current["big_ratio"]) - float(previous["big_ratio"])
        text += f"（週{sign_of(ratio_delta)}{ratio_delta:.2f}pp）"
        if previous.get("total_holders"):
            holders_pct = (float(current["total_holders"]) - float(previous["total_holders"])) / float(
                previous["total_holders"]
            ) * 100
            holders_text += f"（週{sign_of(holders_pct)}{holders_pct:.1f}%）"
    return f"{text}｜{holders_text}"
