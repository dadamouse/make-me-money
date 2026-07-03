"""FastAPI 入口：LINE webhook、健康檢查、每日快照端點、網頁版持股頁、啟動時同步股票對照表。"""
import hmac
import json
import logging
import re
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse

from .chart import ChartStore, render_kline_png
from .config import Settings, load_settings
from .deps import Deps
from .handlers import build_portfolio_entries, handle_command
from .history import get_price_history
from .line_client import LineClient, verify_signature
from .parser import parse_command, summarize_portfolio
from .pending import PendingChoices
from .snapshot import run_snapshot
from .supabase import SupabaseClient
from .twse import TwseClient, sync_stocks
from .webview import render_portfolio_html, verify_portfolio_sig

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
        )
        try:
            count = await sync_stocks(app.state.deps.db, app.state.deps.twse)
            logger.info("已同步上市股票對照表 %s 筆", count)
        except Exception:
            logger.exception("啟動時同步股票對照表失敗，將沿用資料庫既有資料")
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
            png = render_kline_png(history, f"{stock_no} {stock['name']}")
            deps.charts.put(png, key=cache_key)
        return Response(content=png, media_type="image/png")

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
    async def line_webhook(request: Request) -> dict:
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
            cmd = parse_command(event["message"]["text"])
            try:
                reply_text = await handle_command(request.app.state.deps, line_user_id, cmd)
            except Exception as error:
                logger.exception("指令處理失敗 cmd=%s", cmd)
                reply_text = f"⚠️ 系統錯誤：{error}"
            try:
                await request.app.state.line.reply(event["replyToken"], reply_text)
                handled += 1
            except httpx.HTTPError:
                logger.exception("LINE 回覆失敗 cmd=%s", cmd)
        return {"ok": True, "handled": handled}

    return app


app = create_app()
