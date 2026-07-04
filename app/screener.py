"""每日選股：呼叫 Supabase SQL functions，組出附說明的選股報告。"""
import logging
from datetime import datetime, timedelta, timezone

from .deps import Deps
from .parser import format_number

logger = logging.getLogger(__name__)

_TAIPEI_TZ = timezone(timedelta(hours=8))
_STREAK_DAYS = 3
_LIMIT = 3  # 每策略每市場前 N 名
_MARKETS = ("上市", "上櫃")


def _lots(shares: float) -> str:
    value = round(float(shares) / 1000)
    return f"{'+' if value >= 0 else ''}{format_number(value)}"


def _format_streak(row: dict) -> str:
    who = "、".join(
        label for label, hit in (("外資", row.get("foreign_streak")), ("投信", row.get("trust_streak"))) if hit
    )
    return f"{row['stock_no']} {row.get('stock_name') or ''}（{who}連買，{_STREAK_DAYS}日合計 {_lots(row['sum_net'])} 張）"


def _format_co_buy(row: dict) -> str:
    return (
        f"{row['stock_no']} {row.get('stock_name') or ''}"
        f"（外資 {_lots(row['foreign_net'])} 張、投信 {_lots(row['trust_net'])} 張）"
    )


def _format_breakout(row: dict) -> str:
    ratio = float(row["volume"]) / float(row["avg_volume"]) if float(row["avg_volume"]) else 0
    return (
        f"{row['stock_no']} {row.get('stock_name') or ''}"
        f"（收 {format_number(float(row['close']))} 創20日新高，量為5日均量 {ratio:.1f} 倍）"
    )


def _format_kd(row: dict) -> str:
    return (
        f"{row['stock_no']} {row.get('stock_name') or ''}"
        f"（K {float(row['k_val']):.0f} 上穿 D {float(row['d_val']):.0f}）"
    )


_STRATEGIES = (
    {
        "title": f"法人連買 {_STREAK_DAYS} 日",
        "desc": f"外資或投信連續 {_STREAK_DAYS} 個交易日買超，依合計買超排序",
        "rpc": "institutional_streak_picks",
        "args": {"days": _STREAK_DAYS, "limit_n": _LIMIT},
        "depth_key": "insti_days",
        "min_depth": _STREAK_DAYS,
        "format": _format_streak,
    },
    {
        "title": "外資投信同買",
        "desc": "最新交易日外資與投信同步買超，共識較強",
        "rpc": "co_buy_picks",
        "args": {"limit_n": _LIMIT},
        "depth_key": "insti_days",
        "min_depth": 1,
        "format": _format_co_buy,
    },
    {
        "title": "帶量突破 20 日新高",
        "desc": "收盤創 20 日新高且量能放大：上市 >5日均量 3 倍（保底 1,000 張）／上櫃 >5日均量 10 倍（保底 300 張）",
        "rpc": "breakout_picks",
        "args": {"limit_n": _LIMIT},
        "market_args": {
            "上市": {"min_volume": 1_000_000, "vol_multiple": 3},
            "上櫃": {"min_volume": 300_000, "vol_multiple": 10},
        },
        "depth_key": "close_days",
        "min_depth": 21,
        "format": _format_breakout,
    },
    {
        "title": "KD 低檔黃金交叉",
        "desc": "昨日 K<30 且 K≤D，今日 K 上穿 D（超賣區反轉訊號）",
        "rpc": "kd_golden_cross_picks",
        "args": {"limit_n": _LIMIT},
        "depth_key": "close_days",
        "min_depth": 15,
        "format": _format_kd,
    },
)


async def run_daily_picks(deps: Deps) -> dict:
    """每個策略分上市/上櫃各自排名（規模與流動性差異大，混排會被上市大型股洗榜）。"""
    depth_rows = await deps.db.rpc("snapshot_depth", {})
    depth = depth_rows[0] if depth_rows else {}
    sections = []
    for strategy in _STRATEGIES:
        have = int(depth.get(strategy["depth_key"]) or 0)
        if have < strategy["min_depth"]:
            sections.append(
                {
                    "title": strategy["title"],
                    "desc": strategy["desc"],
                    "markets": [],
                    "skipped": f"資料累積中（需 {strategy['min_depth']} 個交易日，目前 {have}）",
                }
            )
            continue
        market_groups = []
        for market in _MARKETS:
            args = {**strategy["args"], "p_market": market, **strategy.get("market_args", {}).get(market, {})}
            try:
                rows = await deps.db.rpc(strategy["rpc"], args)
            except Exception:
                logger.warning("選股策略執行失敗 %s market=%s", strategy["rpc"], market, exc_info=True)
                rows = []
            market_groups.append({"market": market, "picks": [strategy["format"](row) for row in rows]})
        sections.append(
            {"title": strategy["title"], "desc": strategy["desc"], "markets": market_groups, "skipped": None}
        )
    return {"date": datetime.now(_TAIPEI_TZ).date().isoformat(), "sections": sections}


def has_picks(result: dict) -> bool:
    return any(
        group["picks"] for section in result["sections"] for group in section["markets"]
    )


def format_picks_message(result: dict) -> str:
    lines = [f"🎯 每日選股（{result['date'][5:].replace('-', '/')}）"]
    for section in result["sections"]:
        lines.append("")
        lines.append(f"【{section['title']}】")
        lines.append(f"📋 {section['desc']}")
        if section["skipped"]:
            lines.append(f"⏳ {section['skipped']}")
            continue
        for group in section["markets"]:
            lines.append(f"▍{group['market']}")
            if group["picks"]:
                lines += [f"・{pick}" for pick in group["picks"]]
            else:
                lines.append("・無符合標的")
    lines.append("")
    lines.append("⚠️ 技術面策略以已累積足夠歷史的個股為母體；僅供參考，非投資建議")
    return "\n".join(lines)
