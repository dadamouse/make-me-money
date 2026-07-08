"""K 線圖繪製（mplfinance）與圖片暫存（記憶體、TTL）。"""
import io
import time
import uuid

import matplotlib

matplotlib.use("Agg")
import mplfinance as mpf  # noqa: E402
import pandas as pd  # noqa: E402

from .indicators import kd_series, rsi_series  # noqa: E402

CHART_TTL_SECONDS = 900
CHART_ASPECT_RATIO = "16:13"  # 對應 figratio，供 Flex hero 使用

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

    def put(self, png: bytes, key: str | None = None) -> str:
        """未指定 key 時產生隨機 id；指定 key 可作為快取（如網頁版即時圖）。"""
        self._prune()
        chart_id = key or uuid.uuid4().hex
        self._items[chart_id] = (self._clock(), png)
        return chart_id

    def get(self, chart_id: str) -> bytes | None:
        self._prune()
        item = self._items.get(chart_id)
        return item[1] if item else None


def _nan_filled(series: list) -> list[float]:
    return [value if value is not None else float("nan") for value in series]


def _indicator_addplots(rows: list[dict]) -> tuple[list, tuple]:
    """KDJ 與 RSI 副面板；資料不足的面板自動省略。回傳 (addplots, panel_ratios)。"""
    addplots = []
    panel_ratios = [3, 1]  # 主圖、成交量
    next_panel = 2

    k_values, d_values, j_values = kd_series(rows)
    if any(value is not None for value in k_values):
        addplots += [
            mpf.make_addplot(_nan_filled(k_values), panel=next_panel, color="#FB8C00", width=1, ylabel="KDJ"),
            mpf.make_addplot(_nan_filled(d_values), panel=next_panel, color="#1E88E5", width=1),
            mpf.make_addplot(_nan_filled(j_values), panel=next_panel, color="#8E24AA", width=1),
        ]
        panel_ratios.append(1)
        next_panel += 1

    rsi_values = rsi_series([row["close"] for row in rows])
    if any(value is not None for value in rsi_values):
        addplots.append(mpf.make_addplot(_nan_filled(rsi_values), panel=next_panel, color="#6D4C41", width=1, ylabel="RSI"))
        panel_ratios.append(1)

    return addplots, tuple(panel_ratios)


def render_index_png(rows: list[dict], title: str = "加權指數") -> bytes:
    """大盤走勢圖：rows 由舊到新 {trade_date, taiex, amount} → 指數線＋MA20/MA60＋成交金額柱。"""
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt

    dates = [pd.to_datetime(r["trade_date"]) for r in rows]
    closes = [float(r["taiex"]) for r in rows]
    amounts = [float(r["amount"]) / 1e8 if r.get("amount") is not None else 0.0 for r in rows]  # 億元

    def moving_average(n: int) -> list[float | None]:
        return [sum(closes[i - n + 1 : i + 1]) / n if i >= n - 1 else None for i in range(len(closes))]

    with plt.rc_context(
        {"font.sans-serif": ["Noto Sans CJK TC", "PingFang TC", "Heiti TC", "Arial Unicode MS", "sans-serif"],
         "axes.unicode_minus": False}
    ):
        fig, (ax_price, ax_volume) = plt.subplots(
            2, 1, figsize=(9.6, 6.4), sharex=True, gridspec_kw={"height_ratios": [3, 1]}, dpi=110
        )
        up = closes[-1] >= closes[0]
        ax_price.plot(dates, closes, color="#E53935" if up else "#43A047", linewidth=1.6, label="指數")
        for n, color in ((20, "#1E88E5"), (60, "#FB8C00")):
            ma = moving_average(n)
            if any(v is not None for v in ma):
                ax_price.plot(dates, [v if v is not None else float("nan") for v in ma],
                              color=color, linewidth=1, label=f"MA{n}")
        ax_price.legend(loc="upper left", fontsize=8)
        ax_price.set_title(title)
        ax_price.grid(alpha=0.3)
        bar_colors = ["#E53935" if i > 0 and closes[i] >= closes[i - 1] else "#43A047" for i in range(len(closes))]
        ax_volume.bar(dates, amounts, color=bar_colors, width=0.8)
        ax_volume.set_ylabel("成交金額(億)")
        ax_volume.grid(alpha=0.3)
        ax_volume.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d"))
        fig.tight_layout()
        buffer = io.BytesIO()
        fig.savefig(buffer, format="png", bbox_inches="tight")
        plt.close(fig)
    return buffer.getvalue()


def render_kline_png(rows: list[dict], title: str) -> bytes:
    """rows 由舊到新：{trade_date, open, high, low, close, volume}
    → K 線＋MA＋成交量＋KDJ＋RSI 多面板 PNG。
    """
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

    addplots, panel_ratios = _indicator_addplots(rows)
    buffer = io.BytesIO()
    mav = tuple(n for n in (5, 20, 60) if len(df) >= n) or None
    optional_kwargs = {"addplot": addplots, "panel_ratios": panel_ratios} if addplots else {}
    mpf.plot(
        df,
        type="candle",
        mav=mav,
        volume=True,
        style=_STYLE,
        title=title,
        figratio=(16, 13),
        figscale=1.2,
        tight_layout=True,
        savefig={"fname": buffer, "dpi": 110, "bbox_inches": "tight"},
        **optional_kwargs,
    )
    return buffer.getvalue()
