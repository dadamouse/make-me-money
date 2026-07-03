"""環境變數載入與驗證。"""
import os
from dataclasses import dataclass

REQUIRED_KEYS = (
    "SUPABASE_URL",
    "SUPABASE_SERVICE_ROLE_KEY",
    "LINE_CHANNEL_SECRET",
    "LINE_CHANNEL_ACCESS_TOKEN",
)


@dataclass(frozen=True)
class Settings:
    supabase_url: str
    supabase_service_role_key: str
    line_channel_secret: str
    line_channel_access_token: str
    cron_secret: str = ""  # 選用：每日快照端點的驗證 token，未設定則端點停用


def load_settings() -> Settings:
    missing = [key for key in REQUIRED_KEYS if not os.environ.get(key)]
    if missing:
        raise RuntimeError(f"缺少環境變數：{', '.join(missing)}，請到 HF Space Settings 設定")
    return Settings(
        supabase_url=os.environ["SUPABASE_URL"].rstrip("/"),
        supabase_service_role_key=os.environ["SUPABASE_SERVICE_ROLE_KEY"],
        line_channel_secret=os.environ["LINE_CHANNEL_SECRET"],
        line_channel_access_token=os.environ["LINE_CHANNEL_ACCESS_TOKEN"],
        cron_secret=os.environ.get("CRON_SECRET", ""),
    )
