"""FastAPI 入口：LINE webhook、健康檢查、每日快照端點、啟動時同步股票對照表。"""
import hmac
import json
import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException, Request

from .config import Settings, load_settings
from .handlers import handle_command
from .line_client import LineClient, verify_signature
from .parser import parse_command
from .pending import PendingChoices
from .snapshot import run_snapshot
from .supabase import SupabaseClient
from .twse import TwseClient, sync_stocks

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None, transport: httpx.AsyncBaseTransport | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        cfg = settings or load_settings()
        http = httpx.AsyncClient(transport=transport, timeout=15)
        app.state.settings = cfg
        app.state.db = SupabaseClient(http, cfg.supabase_url, cfg.supabase_service_role_key)
        app.state.twse = TwseClient(http)
        app.state.line = LineClient(http, cfg.line_channel_access_token)
        app.state.pending = PendingChoices()
        try:
            count = await sync_stocks(app.state.db, app.state.twse)
            logger.info("已同步上市股票對照表 %s 筆", count)
        except Exception:
            logger.exception("啟動時同步股票對照表失敗，將沿用資料庫既有資料")
        yield
        await http.aclose()

    app = FastAPI(title="make-me-money", lifespan=lifespan)

    @app.get("/")
    async def health() -> dict:
        return {"status": "ok", "service": "line-stock-bot"}

    @app.post("/admin/daily-snapshot")
    async def daily_snapshot(request: Request) -> dict:
        """由 Supabase pg_cron 每日收盤後呼叫：更新對照表＋寫入全市場收盤快照。"""
        secret = request.app.state.settings.cron_secret
        provided = request.headers.get("x-cron-secret", "")
        if not secret or not hmac.compare_digest(secret, provided):
            raise HTTPException(status_code=403, detail="invalid cron secret")
        stocks_synced = await sync_stocks(request.app.state.db, request.app.state.twse)
        result = await run_snapshot(request.app.state.db, request.app.state.twse)
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
                reply_text = await handle_command(
                    request.app.state.db,
                    request.app.state.twse,
                    request.app.state.pending,
                    line_user_id,
                    cmd,
                )
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
