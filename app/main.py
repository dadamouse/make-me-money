"""FastAPI 入口：LINE webhook、健康檢查、每日快照端點、網頁版持股頁、啟動時同步股票對照表。"""
import asyncio
import hmac
import json
import logging
import re
from contextlib import asynccontextmanager

import httpx
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse

from .chart import ChartStore, render_kline_png
from .config import Settings, load_settings
from .deps import Deps
from .flex import build_picks_message
from .handlers import DeferredReply, build_portfolio_entries, fetch_chart_institutional, handle_text, picks_web_url
from .indicators import compute_indicators
from .broker_flows import broker_flow_text, sync_broker_flows
from .history import get_price_history
from .holders import holders_summary_line, sync_holders
from .line_client import LineClient, verify_signature
from .market_calendar import is_scheduled_closed_today, taipei_today_iso
from .market_health import build_market_health_message
from .parser import summarize_portfolio
from .pending import PendingChoices
from .premarket import build_open_brief
from .screener import format_picks_message, has_picks, run_daily_picks
from .snapshot import run_snapshot
from .supabase import SupabaseClient
from .twse import TwseClient, sync_stocks
from .webview import (
    render_picks_html,
    render_portfolio_html,
    render_stock_html,
    verify_picks_sig,
    verify_portfolio_sig,
    verify_stock_sig,
)
from .weekly import build_weekly_outlook, build_weekly_report

_STOCK_NO_PATTERN = re.compile(r"[0-9]{4,6}[A-Z]?")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None, transport: httpx.AsyncBaseTransport | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        cfg = settings or load_settings()
        http = httpx.AsyncClient(transport=transport, timeout=15)
        app.state.settings = cfg
        app.state.line = LineClient(http, cfg.line_channel_access_token)
        app.state.deps = Deps(
            db=SupabaseClient(http, cfg.supabase_url, cfg.supabase_service_role_key),
            twse=TwseClient(http),
            pending=PendingChoices(),
            charts=ChartStore(),
            base_url=cfg.base_url,
            sign_key=cfg.line_channel_secret,
            http=http,
        )
        async def startup_sync() -> None:
            """對照表同步移至背景＋總時限：外部 API 再慢再壞也不能擋住服務啟動。"""
            try:
                count = await asyncio.wait_for(sync_stocks(app.state.deps.db, app.state.deps.twse), timeout=300)
                logger.info("已同步上市股票對照表 %s 筆", count)
            except Exception:
                logger.exception("啟動背景同步失敗，沿用資料庫既有資料（每日排程會補跑）")

        app.state.startup_sync_task = asyncio.create_task(startup_sync())
        yield
        await http.aclose()

    app = FastAPI(title="make-me-money", lifespan=lifespan)

    @app.get("/")
    async def health() -> dict:
        return {"status": "ok", "service": "line-stock-bot"}

    @app.get("/charts/{chart_id}.png")
    async def get_chart(chart_id: str, request: Request) -> Response:
        png = request.app.state.deps.charts.get(chart_id)
        if not png:
            raise HTTPException(status_code=404, detail="chart not found or expired")
        return Response(content=png, media_type="image/png")

    @app.get("/p/{member_id}")
    async def portfolio_page(member_id: int, request: Request, sig: str = "") -> HTMLResponse:
        deps = request.app.state.deps
        if not verify_portfolio_sig(member_id, deps.sign_key, sig):
            raise HTTPException(status_code=403, detail="invalid signature")
        members = await deps.db.get(f"members?id=eq.{member_id}&select=id,name")
        if not members:
            raise HTTPException(status_code=404, detail="member not found")
        entries = await build_portfolio_entries(deps, members[0])
        summary = summarize_portfolio(entries)
        return HTMLResponse(
            render_portfolio_html(members[0]["name"], summary["items"], summary["total_value"], summary["total_pnl"])
        )

    def _prefetch_pick_charts(request: Request, result: dict) -> None:
        """背景優先回補入選股的歷史（讓選股網頁的圖表儘快可用）。"""
        deps = request.app.state.deps
        codes = sorted(
            {
                pick["stock_no"]
                for section in result["sections"]
                for group in section.get("markets", [])
                for pick in group["picks"]
            }
        )
        if not codes:
            return

        async def run() -> None:
            rows = await deps.db.get(f"stocks?stock_no=in.({','.join(codes)})&select=stock_no,market")
            market_map = {r["stock_no"]: r.get("market") for r in rows}
            for code in codes:
                try:
                    await get_price_history(deps.db, deps.twse, code, market_map.get(code))
                except Exception:
                    logger.warning("入選股回補失敗 stock_no=%s", code, exc_info=True)

        request.app.state.prefetch_task = asyncio.create_task(run())

    @app.get("/picks")
    async def picks_page(request: Request, sig: str = "") -> HTMLResponse:
        """每日選股網頁版（入選個股附技術分析圖）。"""
        deps = request.app.state.deps
        if not verify_picks_sig(deps.sign_key, sig):
            raise HTTPException(status_code=403, detail="invalid signature")
        result = await run_daily_picks(deps)
        _prefetch_pick_charts(request, result)
        return HTMLResponse(render_picks_html(result))

    @app.get("/s/{stock_no}")
    async def stock_page(stock_no: str, request: Request, sig: str = "") -> HTMLResponse:
        """單檔個股網頁：現價、指標、法人、資券＋技術分析圖。"""
        deps = request.app.state.deps
        if not verify_stock_sig(stock_no, deps.sign_key, sig):
            raise HTTPException(status_code=403, detail="invalid signature")
        stocks = await deps.db.get(f"stocks?stock_no=eq.{stock_no}&select=stock_no,name,industry,market")
        stock = stocks[0] if stocks else {"stock_no": stock_no, "name": stock_no, "market": None, "industry": None}
        history = await get_price_history(deps.db, deps.twse, stock_no, stock.get("market"))
        if not history:
            raise HTTPException(status_code=404, detail="no data")
        margins = await deps.db.get(
            f"daily_margins?stock_no=eq.{stock_no}&order=trade_date.desc&limit=1"
        )
        institutional = await deps.db.get(
            f"daily_institutional?stock_no=eq.{stock_no}&order=trade_date.desc&limit=1"
        )
        entry = {
            **stock,
            "quote": {"date": history[-1]["trade_date"], "close": history[-1]["close"]},
            "indicators": compute_indicators(history),
            "margin": margins[0] if margins else None,
            "institutional": institutional[0] if institutional else None,
            "holders_line": await holders_summary_line(deps, stock_no),
        }
        flows = await deps.db.get(f"daily_broker_flows?stock_no=eq.{stock_no}&order=trade_date.desc&limit=1")
        entry["broker_flow_line"] = broker_flow_text(flows[0]) if flows else None
        return HTMLResponse(render_stock_html(entry))

    @app.get("/stock-chart/{stock_no}.png")
    async def stock_chart_image(stock_no: str, request: Request) -> Response:
        """網頁版即時線圖（記憶體快取 15 分鐘）。"""
        deps = request.app.state.deps
        if not _STOCK_NO_PATTERN.fullmatch(stock_no):
            raise HTTPException(status_code=404, detail="invalid stock no")
        cache_key = f"live:{stock_no}"
        png = deps.charts.get(cache_key)
        if not png:
            stocks = await deps.db.get(f"stocks?stock_no=eq.{stock_no}&select=stock_no,name,market")
            stock = stocks[0] if stocks else {"stock_no": stock_no, "name": stock_no, "market": None}
            history = await get_price_history(deps.db, deps.twse, stock_no, stock.get("market"))
            if len(history) < 5:
                raise HTTPException(status_code=404, detail="insufficient history")
            png = render_kline_png(
                history, f"{stock_no} {stock['name']}",
                institutional=await fetch_chart_institutional(deps, stock_no),
            )
            deps.charts.put(png, key=cache_key)
        return Response(content=png, media_type="image/png")

    def _check_cron_secret(request: Request) -> None:
        secret = request.app.state.settings.cron_secret
        provided = request.headers.get("x-cron-secret", "")
        if not secret or not hmac.compare_digest(secret, provided):
            raise HTTPException(status_code=403, detail="invalid cron secret")

    async def _start_backfill(request: Request) -> dict:
        state = request.app.state
        status = getattr(state, "backfill_status", None)
        if status and status.get("running"):
            return status
        deps = state.deps
        # Supabase 單次查詢上限 1000 列，分頁撈全表
        stocks: list[dict] = []
        page_size = 1000
        while True:
            page = await deps.db.get(
                f"stocks?select=stock_no,market&order=stock_no&limit={page_size}&offset={len(stocks)}"
            )
            stocks += page
            if len(page) < page_size:
                break
        state.backfill_status = {"running": True, "total": len(stocks), "done": 0, "errors": 0}

        async def run() -> None:
            for stock in stocks:
                try:
                    await get_price_history(deps.db, deps.twse, stock["stock_no"], stock.get("market"))
                except Exception:
                    state.backfill_status["errors"] += 1
                    logger.warning("歷史回補失敗 stock_no=%s", stock["stock_no"], exc_info=True)
                state.backfill_status["done"] += 1
                if state.backfill_status["done"] % 100 == 0:
                    logger.info("歷史回補進度 %s", state.backfill_status)
            state.backfill_status["running"] = False
            logger.info("歷史回補完成 %s", state.backfill_status)

        # 保留 task 引用避免被 GC（asyncio.create_task 的已知陷阱）
        state.backfill_task = asyncio.create_task(run())
        return state.backfill_status

    async def _push_personal(request: Request, builder) -> int:
        """對每位已綁定成員，各自產生內容並推播。"""
        deps = request.app.state.deps
        bindings = await deps.db.get("line_bindings?select=line_user_id,member_id")
        pushed = 0
        for binding in bindings:
            if not binding.get("member_id"):
                continue
            members = await deps.db.get(f"members?id=eq.{binding['member_id']}&select=id,name")
            if not members:
                continue
            text = await builder(deps, members[0])
            if not text:
                continue
            try:
                await request.app.state.line.push(binding["line_user_id"], text)
                pushed += 1
            except httpx.HTTPError:
                logger.warning("推播失敗 user=%s", binding["line_user_id"], exc_info=True)
        return pushed

    async def _broadcast(request: Request, message: str | dict) -> int:
        deps = request.app.state.deps
        bindings = await deps.db.get("line_bindings?select=line_user_id")
        user_ids = [b["line_user_id"] for b in bindings]
        if not user_ids:
            return 0
        await request.app.state.line.multicast(user_ids, message)
        return len(user_ids)

    @app.post("/admin/morning-macro")
    async def morning_macro(request: Request) -> dict:
        """已併入 /admin/morning-open（盤前導航），保留端點避免舊 pg_cron 打到 404，但不再推播。"""
        _check_cron_secret(request)
        logger.info("盤前總經快報已併入盤前導航，此端點不推播")
        return {"ok": True, "pushed": 0, "skipped": "merged into morning-open"}

    @app.post("/admin/morning-open")
    async def morning_open(request: Request) -> dict:
        """平日 8:40 前後：盤前導航（總經＋試撮＋昨日台股＋除權息）。國定假日或無試撮（颱風臨時休市）跳過。"""
        _check_cron_secret(request)
        if await is_scheduled_closed_today(request.app.state.deps.http):
            logger.info("今日台股休市（國定假日），跳過盤前導航")
            return {"ok": True, "pushed": 0, "skipped": "market closed"}
        brief = await build_open_brief(request.app.state.deps)
        if brief is None:
            logger.info("無試撮資料（颱風臨時休市或 MIS 異常），跳過盤前導航")
            return {"ok": True, "pushed": 0, "skipped": "no trial data"}
        pushed = await _broadcast(request, brief)
        logger.info("盤前導航 pushed=%s", pushed)
        return {"ok": True, "pushed": pushed}

    @app.post("/admin/sync-broker-flows")
    async def sync_broker_flows_endpoint(request: Request) -> dict:
        """平日晚間：抓持股的主力買賣超（MoneyDJ 分點頁，僅持股清單、間隔 2 秒）。"""
        _check_cron_secret(request)
        result = await sync_broker_flows(request.app.state.deps)
        logger.info("主力買賣超同步完成 %s", result)
        return {"ok": True, **result}

    @app.post("/admin/sync-holders")
    async def sync_holders_endpoint(request: Request) -> dict:
        """每週六早上：同步 TDCC 集保股權分散（千張大戶比、股東人數）。"""
        _check_cron_secret(request)
        result = await sync_holders(request.app.state.deps)
        logger.info("集保資料同步完成 %s", result)
        return {"ok": True, **result}

    @app.post("/admin/weekly-report")
    async def weekly_report(request: Request) -> dict:
        """週六早上：持股週報推播。"""
        _check_cron_secret(request)
        pushed = await _push_personal(request, build_weekly_report)
        logger.info("持股週報完成 pushed=%s", pushed)
        return {"ok": True, "pushed": pushed}

    @app.post("/admin/weekly-outlook")
    async def weekly_outlook(request: Request) -> dict:
        """週日早上：下週展望推播＋觸發回補健檢。"""
        _check_cron_secret(request)
        pushed = await _push_personal(request, build_weekly_outlook)
        backfill = await _start_backfill(request)
        logger.info("下週展望完成 pushed=%s backfill=%s", pushed, backfill)
        return {"ok": True, "pushed": pushed, "backfill": backfill}

    @app.get("/admin/backfill-history")
    async def backfill_history_status(request: Request) -> dict:
        """查詢背景回補進度（不觸發）。"""
        _check_cron_secret(request)
        default = {"running": False, "total": 0, "done": 0, "errors": 0}
        return {"ok": True, **getattr(request.app.state, "backfill_status", default)}

    @app.post("/admin/backfill-history")
    async def backfill_history(request: Request) -> dict:
        """背景回補全市場歷史價（受節流器保護、可中斷重跑：已足夠的個股會跳過）。"""
        _check_cron_secret(request)
        return {"ok": True, **await _start_backfill(request)}

    @app.post("/admin/daily-picks")
    async def daily_picks(request: Request) -> dict:
        """由 pg_cron 於晚間快照後呼叫：跑選股並推播給所有綁定的使用者。"""
        secret = request.app.state.settings.cron_secret
        provided = request.headers.get("x-cron-secret", "")
        if not secret or not hmac.compare_digest(secret, provided):
            raise HTTPException(status_code=403, detail="invalid cron secret")
        deps = request.app.state.deps
        today = taipei_today_iso()
        latest_rows = await deps.db.get("daily_closes?select=trade_date&order=trade_date.desc&limit=1")
        latest_date = str(latest_rows[0]["trade_date"]) if latest_rows else None
        if latest_date != today:
            logger.info("今日無新收盤資料（休市或快照失敗），跳過選股推播 latest=%s", latest_date)
            return {"ok": True, "pushed": 0, "skipped": "no fresh close data", "date": today}
        result = await run_daily_picks(deps)
        pushed = 0
        if has_picks(result):
            bindings = await deps.db.get("line_bindings?select=line_user_id")
            user_ids = [b["line_user_id"] for b in bindings]
            if user_ids:
                health = await build_market_health_message(deps)
                message = build_picks_message(result, format_picks_message(result), picks_web_url(deps))
                await request.app.state.line.multicast(user_ids, [health, message])
                pushed = len(user_ids)
                _prefetch_pick_charts(request, result)  # 推播後先把入選股的圖備好
        logger.info("每日選股完成 pushed=%s", pushed)
        return {"ok": True, "pushed": pushed, "date": result["date"]}

    @app.post("/admin/daily-snapshot")
    async def daily_snapshot(request: Request) -> dict:
        """由 Supabase pg_cron 每日收盤後呼叫：更新對照表＋寫入全市場收盤快照。"""
        secret = request.app.state.settings.cron_secret
        provided = request.headers.get("x-cron-secret", "")
        if not secret or not hmac.compare_digest(secret, provided):
            raise HTTPException(status_code=403, detail="invalid cron secret")
        stocks_synced = await sync_stocks(request.app.state.deps.db, request.app.state.deps.twse)
        result = await run_snapshot(request.app.state.deps.db, request.app.state.deps.twse)
        logger.info("每日快照完成 stocks=%s result=%s", stocks_synced, result)
        return {"ok": True, "stocks_synced": stocks_synced, **result}

    @app.post("/webhook/line")
    async def line_webhook(request: Request, background_tasks: BackgroundTasks) -> dict:
        body = await request.body()
        signature = request.headers.get("x-line-signature")
        if not verify_signature(request.app.state.settings.line_channel_secret, body, signature):
            raise HTTPException(status_code=403, detail="invalid line signature")

        payload = json.loads(body)
        handled = 0
        for event in payload.get("events", []):
            if event.get("type") != "message" or (event.get("message") or {}).get("type") != "text":
                continue
            line_user_id = (event.get("source") or {}).get("userId")
            text = event["message"]["text"]
            try:
                result = await handle_text(request.app.state.deps, line_user_id, text)
            except Exception as error:
                logger.exception("指令處理失敗 text=%s", text[:100])
                result = f"⚠️ 系統錯誤：{error}"

            if isinstance(result, DeferredReply):
                # 先立刻回覆 ack，完成後用 push 送真正內容（避免 replyToken 30 秒過期）
                try:
                    await request.app.state.line.reply(event["replyToken"], result.ack_text)
                    handled += 1
                except httpx.HTTPError:
                    logger.exception("LINE ack 回覆失敗 text=%s", text[:100])

                async def _push_deferred(uid: str, deferred: DeferredReply) -> None:
                    try:
                        content = await deferred.builder()
                        await request.app.state.line.push(uid, content)
                    except Exception:
                        logger.exception("DeferredReply push 失敗 user=%s", uid)

                background_tasks.add_task(_push_deferred, line_user_id, result)
            else:
                try:
                    await request.app.state.line.reply(event["replyToken"], result)
                    handled += 1
                except httpx.HTTPError:
                    logger.exception("LINE 回覆失敗 text=%s", text[:100])
        return {"ok": True, "handled": handled}

    return app


app = create_app()
