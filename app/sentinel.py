"""哨兵：狀態改變才推播的預警系統（盤後掃描＋盤中急跌警報）。

設計原則：
- 只推「昨天沒有、今天有」的狀態改變，不重複轟炸（alert_log 再擋同日重跑）
- 大盤警報廣播給所有人；持股警訊只推給持有該股的成員
- 2026-07 中旬大跌全程靜默的教訓：工具知道的事必須主動說
"""
import asyncio
import logging

from .deps import Deps
from .history import read_batch
from .indicators import kd_series, macd_series, sma
from .market_calendar import taipei_today_iso
from .parser import format_number, sign_of
from .premarket import fetch_close_series, fetch_trial_quotes
from .support_break import evaluate_signals

logger = logging.getLogger(__name__)

_MIN_ROWS = 30            # 指標狀態至少需要的日線數
_KD_HOT, _KD_COLD = 70.0, 30.0
_MARKET_DROP_PCT = -2.0   # 大盤單日跌幅警戒
_STOCK_DROP_PCT = -5.0    # 盤中個股跌幅警戒
_VIX_WARN = 25.0
_INSTI_SELL_DAYS = 3


def _stock_state(rows: list[dict], market: str | None) -> dict | None:
    """單一個股的可比較狀態快照（供今昨對比）。"""
    closes = [r["close"] for r in rows if r.get("close") is not None]
    if len(closes) < _MIN_ROWS:
        return None
    ma20 = sma(closes, 20)
    dif, dea, _ = macd_series(closes)
    k_series, d_series, _ = kd_series(rows)
    signals = evaluate_signals(rows, market) or {"sell": [], "buy": []}
    return {
        "above_ma20": ma20 is not None and closes[-1] >= ma20,
        "ma20": ma20,
        "close": closes[-1],
        "macd_bull": dif[-1] > dea[-1] if dif[-1] is not None and dea[-1] is not None else None,
        "k": k_series[-1],
        "d": d_series[-1],
        "sell_on": {s["code"] for s in signals["sell"] if s["on"]},
        "sell_labels": {s["code"]: s["label"] for s in signals["sell"]},
    }


def diff_stock_alerts(stock: dict, today: dict, yesterday: dict) -> list[tuple[str, str]]:
    """比較今昨狀態 → [(alert_key_suffix, 訊息行)]；只回報新發生的變化。"""
    label = f"{stock['stock_no']} {stock['name']}"
    close_text = format_number(today["close"])
    alerts = []
    for code in sorted(today["sell_on"] - yesterday["sell_on"]):
        alerts.append((f"sig:{code}", f"🚨 {label}：{code} {today['sell_labels'].get(code, '')} 成立（收 {close_text}）"))
    if yesterday["above_ma20"] and not today["above_ma20"]:
        alerts.append(("ma20:break", f"🚨 {label}：跌破月線（收 {close_text}＜月線 {format_number(today['ma20'])}）"))
    if not yesterday["above_ma20"] and today["above_ma20"]:
        alerts.append(("ma20:reclaim", f"✅ {label}：站回月線（收 {close_text}＞月線 {format_number(today['ma20'])}）"))
    if yesterday.get("macd_bull") is True and today.get("macd_bull") is False:
        alerts.append(("macd:bear", f"🚨 {label}：MACD 翻空（DIF 跌破訊號線）"))
    if yesterday.get("macd_bull") is False and today.get("macd_bull") is True:
        alerts.append(("macd:bull", f"✅ {label}：MACD 翻多（DIF 站上訊號線）"))
    kd_ready = all(v is not None for v in (today["k"], today["d"], yesterday["k"], yesterday["d"]))
    if kd_ready:
        if yesterday["k"] >= yesterday["d"] and today["k"] < today["d"] and yesterday["k"] >= _KD_HOT:
            alerts.append(("kd:death", f"🚨 {label}：KD 高檔死叉（K {today['k']:.0f} 下穿 D，自 {yesterday['k']:.0f} 高檔轉弱）"))
        if yesterday["k"] <= yesterday["d"] and today["k"] > today["d"] and yesterday["k"] <= _KD_COLD:
            alerts.append(("kd:golden", f"✅ {label}：KD 低檔金叉（K {today['k']:.0f} 上穿 D）"))
    return alerts


def _insti_streak(totals: list[float]) -> int:
    """totals 新到舊；回傳開頭連續賣超天數。"""
    streak = 0
    for value in totals:
        if value < 0:
            streak += 1
        else:
            break
    return streak


async def _holdings_stock_alerts(deps: Deps, codes: list[str], info_map: dict) -> dict[str, list[tuple[str, str]]]:
    """每檔持股的盤後警訊：{stock_no: [(key_suffix, line)]}。"""
    histories = await read_batch(deps.db, codes)
    insti_raw = await deps.db.get(
        f"daily_institutional?stock_no=in.({','.join(codes)})&select=stock_no,trade_date,foreign_net,trust_net,dealer_net"
        f"&order=trade_date.desc&limit={len(codes) * (_INSTI_SELL_DAYS + 2)}"
    )
    insti_by_stock: dict[str, list[float]] = {}
    for row in insti_raw:
        nets = [row.get(key) for key in ("foreign_net", "trust_net", "dealer_net")]
        if all(v is None for v in nets):
            continue
        insti_by_stock.setdefault(str(row["stock_no"]), []).append(sum(float(v) for v in nets if v is not None))

    result: dict[str, list[tuple[str, str]]] = {}
    for code in codes:
        stock = info_map.get(code, {"stock_no": code, "name": code, "market": None})
        rows = histories.get(code, [])
        today = _stock_state(rows, stock.get("market"))
        yesterday = _stock_state(rows[:-1], stock.get("market")) if len(rows) > _MIN_ROWS else None
        alerts = diff_stock_alerts(stock, today, yesterday) if today and yesterday else []
        totals = insti_by_stock.get(code, [])
        if _insti_streak(totals) == _INSTI_SELL_DAYS:  # 剛滿 3 日才推一次
            five_day = sum(totals[:_INSTI_SELL_DAYS]) / 1000
            alerts.append((
                "insti:sell3",
                f"🚨 {stock['stock_no']} {stock['name']}：法人連 {_INSTI_SELL_DAYS} 日賣超（累計 {format_number(round(five_day))} 張）",
            ))
        if alerts:
            result[code] = alerts
    return result


async def _market_alerts(deps: Deps) -> list[tuple[str, str]]:
    """大盤層級警報：跌破/站回月線、單日重挫、法人連賣、VIX 跳升（皆為狀態改變）。"""
    alerts: list[tuple[str, str]] = []
    try:
        series_desc = await deps.db.rpc("market_series", {"n": 60})
        closes = [float(r["taiex"]) for r in reversed(series_desc)]
        if len(closes) >= 21:
            ma_today = sum(closes[-20:]) / 20
            ma_yesterday = sum(closes[-21:-1]) / 20
            below_today = closes[-1] < ma_today
            below_yesterday = closes[-2] < ma_yesterday
            if below_today and not below_yesterday:
                alerts.append(("market:below_ma20",
                               f"🚨 大盤跌破月線（收 {format_number(closes[-1])}＜{format_number(ma_today)}）——"
                               "環境轉弱，突破型買訊勝率下降，檢查持股停損、新倉縮小"))
            if not below_today and below_yesterday:
                alerts.append(("market:above_ma20",
                               f"✅ 大盤站回月線（收 {format_number(closes[-1])}）——空方環境解除的第一步，"
                               "確認站穩三日再恢復正常部位"))
            day_pct = (closes[-1] / closes[-2] - 1) * 100
            if day_pct <= _MARKET_DROP_PCT:
                alerts.append(("market:crash",
                               f"🚨 大盤單日重挫 {day_pct:.1f}%（收 {format_number(closes[-1])}）——"
                               "急跌隔日常有反彈但不代表止跌，先看法人動向再動作"))
    except Exception:
        logger.warning("哨兵：大盤資料取得失敗", exc_info=True)
    try:
        flows = await deps.db.rpc("market_flow_series", {"n": _INSTI_SELL_DAYS + 1})
        nets = [float(r["insti_net"]) for r in flows if r.get("insti_net") is not None]
        if len(nets) >= _INSTI_SELL_DAYS and all(n < 0 for n in nets[:_INSTI_SELL_DAYS]) and not (
            len(nets) > _INSTI_SELL_DAYS and nets[_INSTI_SELL_DAYS] < 0
        ):
            total_lots = sum(nets[:_INSTI_SELL_DAYS]) / 1000
            alerts.append(("market:insti_sell3",
                           f"🚨 法人連 {_INSTI_SELL_DAYS} 日賣超全市場（累計 {format_number(round(total_lots))} 張）——"
                           "大資金持續退場中"))
    except Exception:
        logger.warning("哨兵：法人資料取得失敗", exc_info=True)
    try:
        vix = await fetch_close_series(deps.http, "^VIX", range_="5d")
        if len(vix) >= 2 and vix[-1] >= _VIX_WARN > vix[-2]:
            alerts.append(("market:vix",
                           f"🚨 VIX 跳上 {vix[-1]:.1f}（前日 {vix[-2]:.1f}）——國際避險情緒升溫，外資賣壓易放大"))
    except Exception:
        logger.warning("哨兵：VIX 取得失敗", exc_info=True)
    return alerts


async def _intraday_alerts(deps: Deps, codes: list[str], info_map: dict) -> tuple[list, dict]:
    """盤中急跌：大盤 vs 昨收 ≤ -2%、持股即時價 vs 最近收盤 ≤ -5%。"""
    market_alerts: list[tuple[str, str]] = []
    quotes = await fetch_trial_quotes(deps.http)
    t00 = quotes.get("t00") or {}
    if t00.get("last") and t00.get("prev"):
        pct = (t00["last"] / t00["prev"] - 1) * 100
        if pct <= _MARKET_DROP_PCT:
            market_alerts.append(("intraday:market",
                                  f"🚨 盤中警報：大盤現跌 {pct:.1f}%（{format_number(t00['last'])}）——"
                                  "波動放大，掛單前先深呼吸"))
    stock_alerts: dict[str, list[tuple[str, str]]] = {}
    if codes:
        latest = await deps.db.get(
            f"daily_closes?stock_no=in.({','.join(codes)})&select=stock_no,close,trade_date"
            f"&order=stock_no,trade_date.desc&limit={len(codes) * 2}"
        )
        prev_close: dict[str, float] = {}
        for row in latest:
            prev_close.setdefault(str(row["stock_no"]), float(row["close"]))
        for code in codes:
            base = prev_close.get(code)
            if not base:
                continue
            stock = info_map.get(code, {"stock_no": code, "name": code, "market": None})
            realtime = await deps.twse.fetch_realtime(code, stock.get("market"))
            await asyncio.sleep(0.3)
            if not realtime or realtime.get("close") is None:
                continue
            # 收盤後快照已寫入今日時，base 即今日收盤 → pct≈0，不會誤報
            pct = (float(realtime["close"]) / base - 1) * 100
            if pct <= _STOCK_DROP_PCT:
                stock_alerts[code] = [(
                    "intraday:drop",
                    f"🚨 {stock['stock_no']} {stock['name']} 盤中跌 {pct:.1f}%（{format_number(realtime['close'])}）",
                )]
    return market_alerts, stock_alerts


async def _filter_new_alerts(deps: Deps, keyed: list[tuple[str, str]]) -> list[str]:
    """alert_log 去重（同鍵同日只推一次），回傳新警報的訊息行。"""
    if not keyed:
        return []
    today = taipei_today_iso()
    existing = {
        row["alert_key"]
        for row in await deps.db.get(f"alert_log?alert_date=eq.{today}&select=alert_key")
    }
    fresh = [(key, line) for key, line in keyed if key not in existing]
    if fresh:
        await deps.db.insert(
            "alert_log?on_conflict=alert_key,alert_date",
            [{"alert_key": key, "alert_date": today} for key, _ in fresh],
            prefer="resolution=merge-duplicates",
        )
    return [line for _, line in fresh]


async def run_sentinel(deps: Deps, mode: str = "close") -> dict:
    """執行哨兵掃描。回傳 {"market": [廣播行], "personal": {line_user_id: 訊息}}。"""
    bindings = await deps.db.get("line_bindings?select=line_user_id,member_id")
    holdings = await deps.db.get("holdings?select=member_id,stock_no")
    member_codes: dict[int, set[str]] = {}
    for row in holdings:
        member_codes.setdefault(row["member_id"], set()).add(str(row["stock_no"]))
    codes = sorted({code for codes_ in member_codes.values() for code in codes_})
    info_map = {}
    if codes:
        stock_rows = await deps.db.get(f"stocks?stock_no=in.({','.join(codes)})&select=stock_no,name,market")
        info_map = {s["stock_no"]: s for s in stock_rows}

    if mode == "intraday":
        market_keyed, stock_alerts = await _intraday_alerts(deps, codes, info_map)
    else:
        market_keyed = await _market_alerts(deps)
        stock_alerts = await _holdings_stock_alerts(deps, codes, info_map) if codes else {}

    market_lines = await _filter_new_alerts(deps, market_keyed)

    personal: dict[str, str] = {}
    for binding in bindings:
        member_id = binding.get("member_id")
        if not member_id or member_id not in member_codes:
            continue
        keyed = [
            (f"{code}:{suffix}", line)
            for code in sorted(member_codes[member_id])
            for suffix, line in stock_alerts.get(code, [])
        ]
        lines = await _filter_new_alerts(deps, [(f"{binding['line_user_id']}:{k}", line) for k, line in keyed])
        if lines:
            title = "🛎 持股盤中警報" if mode == "intraday" else "🛎 持股盤後警訊"
            body = "\n".join(lines)
            personal[binding["line_user_id"]] = (
                f"{title}（{taipei_today_iso()[5:].replace('-', '/')}）\n{body}\n"
                "輸入「賣 代號」看該股完整檢查"
            )
    return {"market": market_lines, "personal": personal}
