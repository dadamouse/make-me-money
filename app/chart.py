"""K 線圖繪製（mplfinance）與圖片暫存（記憶體、TTL）。"""
import io
import time
import uuid

import matplotlib

matplotlib.use("Agg")
import mplfinance as mpf  # noqa: E402
import pandas as pd  # noqa: E402

CHART_TTL_SECONDS = 900

# 台股慣例：紅漲綠跌；中文字型優先用容器內安裝的 Noto CJK
_MARKET_COLORS = mpf.make_marketcolors(up="#E53935", down="#43A047", edge="inherit", wick="inherit", volume="in")
_STYLE = mpf.make_mpf_style(
    base_mpf_style="yahoo",
    marketcolors=_MARKET_COLORS,
    rc={
        "font.sans-serif": ["Noto Sans CJK TC", "PingFang TC", "Heiti TC", "Arial Unicode MS", "sans-serif"],
        "axes.unicode_minus": False,
    },
)


class ChartStore:
    """chart_id → PNG bytes，逾時自動清除。"""

    def __init__(self, ttl_seconds: float = CHART_TTL_SECONDS, clock=time.monotonic):
        self._ttl = ttl_seconds
        self._clock = clock
        self._items: dict[str, tuple[float, bytes]] = {}

    def _prune(self) -> None:
        now = self._clock()
        expired = [key for key, (created_at, _) in self._items.items() if now - created_at > self._ttl]
        for key in expired:
            del self._items[key]

    def put(self, png: bytes) -> str:
        self._prune()
        chart_id = uuid.uuid4().hex
        self._items[chart_id] = (self._clock(), png)
        return chart_id

    def get(self, chart_id: str) -> bytes | None:
        self._prune()
        item = self._items.get(chart_id)
        return item[1] if item else None


def render_kline_png(rows: list[dict], title: str) -> bytes:
    """rows 由舊到新：{trade_date, open, high, low, close, volume} → K 線＋MA＋成交量 PNG。"""
    records = []
    for row in rows:
        close = row["close"]
        records.append(
            {
                "Date": row["trade_date"],
                "Open": row.get("open") if row.get("open") is not None else close,
                "High": row.get("high") if row.get("high") is not None else close,
                "Low": row.get("low") if row.get("low") is not None else close,
                "Close": close,
                "Volume": row.get("volume") or 0,
            }
        )
    df = pd.DataFrame(records)
    df["Date"] = pd.to_datetime(df["Date"])
    df = df.set_index("Date")

    buffer = io.BytesIO()
    mav = tuple(n for n in (5, 20, 60) if len(df) >= n) or None
    mpf.plot(
        df,
        type="candle",
        mav=mav,
        volume=True,
        style=_STYLE,
        title=title,
        figratio=(16, 10),
        figscale=1.2,
        tight_layout=True,
        savefig={"fname": buffer, "dpi": 110, "bbox_inches": "tight"},
    )
    return buffer.getvalue()
