"""K 線圖繪製（mplfinance）與圖片暫存（記憶體、TTL）。"""
import io
import time
import uuid

import matplotlib

matplotlib.use("Agg")
import mplfinance as mpf  # noqa: E402
import pandas as pd  # noqa: E402

from .indicators import bollinger_series, kd_series, macd_series, rsi_series  # noqa: E402

CHART_TTL_SECONDS = 900
CHART_ASPECT_RATIO = "13:10"  # 實際輸出約 953×736（含新副圖），供 Flex hero 使用

# 主圖各線顏色（圖例與繪線共用同一組定義，保證對得上）
_MA_COLORS = {5: "#FB8C00", 20: "#1E88E5", 60: "#8E24AA"}
_BOLL_COLOR = "#90A4AE"
_INSTI_COLORS = {"外資": "#1E88E5", "投信": "#FB8C00", "自營": "#8E24AA"}

# 台股慣例：紅漲綠跌；中文字型優先用容器內安裝的 Noto CJK
_MARKET_COLORS = mpf.make_marketcolors(up="#E53935", down="#43A047", edge="inherit", wick="inherit", volume="in")
_STYLE = mpf.make_mpf_style(
    base_mpf_style="yahoo",
    marketcolors=_MARKET_COLORS,
    mavcolors=[_MA_COLORS[5], _MA_COLORS[20], _MA_COLORS[60]],
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


def _indicator_addplots(rows: list[dict], has_institutional: bool) -> tuple[list, tuple, dict]:
    """KDJ／RSI／MACD 副面板；資料不足的面板自動省略。回傳 (addplots, panel_ratios, 面板編號表)。"""
    addplots = []
    panel_ratios = [4, 1]  # 主圖、成交量
    next_panel = 2
    panels: dict[str, int] = {}
    closes = [row["close"] for row in rows]

    # 布林通道疊在主圖（中軌即 MA20，不重畫）
    boll_upper, _, boll_lower = bollinger_series(closes)
    if any(value is not None for value in boll_upper):
        addplots += [
            mpf.make_addplot(_nan_filled(boll_upper), panel=0, color=_BOLL_COLOR, width=0.9, linestyle="--"),
            mpf.make_addplot(_nan_filled(boll_lower), panel=0, color=_BOLL_COLOR, width=0.9, linestyle="--"),
        ]

    k_values, d_values, j_values = kd_series(rows)
    if any(value is not None for value in k_values):
        addplots += [
            mpf.make_addplot(_nan_filled(k_values), panel=next_panel, color="#FB8C00", width=1, ylabel="KDJ"),
            mpf.make_addplot(_nan_filled(d_values), panel=next_panel, color="#1E88E5", width=1),
            mpf.make_addplot(_nan_filled(j_values), panel=next_panel, color="#8E24AA", width=1),
        ]
        panel_ratios.append(1)
        next_panel += 1

    rsi_values = rsi_series(closes)
    if any(value is not None for value in rsi_values):
        addplots.append(mpf.make_addplot(_nan_filled(rsi_values), panel=next_panel, color="#6D4C41", width=1, ylabel="RSI"))
        panel_ratios.append(1)
        next_panel += 1

    dif, dea, hist = macd_series(closes)
    if any(value is not None for value in hist):
        # 柱狀圖不用 mpf 的 bar addplot（會畫成細碎虛線），改於 returnfig 後用 matplotlib 補實心柱
        addplots.append(mpf.make_addplot(_nan_filled(dif), panel=next_panel, color="#FB8C00", width=1, ylabel="MACD"))
        addplots.append(mpf.make_addplot(_nan_filled(dea), panel=next_panel, color="#1E88E5", width=1))
        panel_ratios.append(1)
        panels["macd"] = next_panel
        next_panel += 1

    if has_institutional:
        # 先用一條隱形零線把面板占住，法人柱狀圖之後用 matplotlib 直接畫（分組柱 mpf 不支援）
        addplots.append(
            mpf.make_addplot([0.0] * len(rows), panel=next_panel, color="#FFFFFF", width=0, alpha=0.0)
        )
        panel_ratios.append(1)
        panels["institutional"] = next_panel

    return addplots, tuple(panel_ratios), panels


def _draw_macd_hist(ax, closes: list[float]) -> None:
    """MACD 柱狀圖（DIF−DEA）：實心紅漲綠跌、貼齊零軸，比照市面看盤軟體。"""
    _, _, hist = macd_series(closes)
    positions = [i for i, value in enumerate(hist) if value is not None]
    values = [hist[i] for i in positions]
    colors = ["#E53935" if value >= 0 else "#43A047" for value in values]
    ax.bar(positions, values, width=0.7, color=colors, alpha=0.85, zorder=0)
    ax.axhline(0, color="#9E9E9E", linewidth=0.6)


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


def _draw_institutional_bars(ax, rows: list[dict], institutional: list[dict]) -> None:
    """法人買賣超堆疊柱（單位：張）：同日買超往上疊、賣超往下疊，對齊 K 棒位置。"""
    net_by_date = {str(item["trade_date"]): item for item in institutional}
    keys = (("foreign_net", "外資"), ("trust_net", "投信"), ("dealer_net", "自營"))
    positions = list(range(len(rows)))
    stack_up = [0.0] * len(rows)
    stack_down = [0.0] * len(rows)
    for key, label in keys:
        lots = [float(net_by_date.get(str(row["trade_date"]), {}).get(key) or 0) / 1000 for row in rows]
        bottoms = [stack_up[i] if lots[i] >= 0 else stack_down[i] for i in positions]
        ax.bar(positions, lots, bottom=bottoms, width=0.8, color=_INSTI_COLORS[label], label=label)
        stack_up = [stack_up[i] + lots[i] if lots[i] >= 0 else stack_up[i] for i in positions]
        stack_down = [stack_down[i] + lots[i] if lots[i] < 0 else stack_down[i] for i in positions]
    ax.set_ylabel("法人(張)")
    ax.axhline(0, color="#9E9E9E", linewidth=0.6)
    ax.legend(loc="upper left", fontsize=7, ncol=3, framealpha=0.6)


def _main_panel_legend(ax, df_len: int, has_bollinger: bool) -> None:
    """主圖下緣圖例：標示各均線與布林軌道的顏色。"""
    from matplotlib.lines import Line2D

    handles = [
        Line2D([0], [0], color=color, linewidth=1.4)
        for n, color in _MA_COLORS.items()
        if df_len >= n
    ]
    labels = [f"MA{n}" for n in _MA_COLORS if df_len >= n]
    if has_bollinger:
        handles.append(Line2D([0], [0], color=_BOLL_COLOR, linewidth=1.4, linestyle="--"))
        labels.append("布林上下軌")
    if handles:
        ax.legend(handles, labels, loc="lower left", fontsize=8, ncol=len(handles), framealpha=0.6)


def render_kline_png(rows: list[dict], title: str, institutional: list[dict] | None = None) -> bytes:
    """rows 由舊到新：{trade_date, open, high, low, close, volume}
    → K 線＋MA＋布林通道＋成交量＋KDJ＋RSI＋MACD＋三大法人 多面板 PNG。
    institutional：{trade_date, foreign_net, trust_net, dealer_net}（股數），缺省略該面板。
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

    has_institutional = bool(institutional)
    addplots, panel_ratios, panels = _indicator_addplots(rows, has_institutional)
    mav = tuple(n for n in (5, 20, 60) if len(df) >= n) or None
    optional_kwargs = {"addplot": addplots, "panel_ratios": panel_ratios} if addplots else {}

    boll_upper, _, boll_lower = bollinger_series([row["close"] for row in rows])
    has_bollinger = any(value is not None for value in boll_upper)
    if has_bollinger:
        optional_kwargs["fill_between"] = {
            "y1": _nan_filled(boll_lower),
            "y2": _nan_filled(boll_upper),
            "alpha": 0.08,
            "color": _BOLL_COLOR,
        }

    fig, axes = mpf.plot(
        df,
        type="candle",
        mav=mav,
        volume=True,
        style=_STYLE,
        title=title,
        figratio=(16, 13),
        figscale=1.2,
        tight_layout=True,
        returnfig=True,
        **optional_kwargs,
    )
    _main_panel_legend(axes[0], len(df), has_bollinger)
    if "macd" in panels:
        _draw_macd_hist(axes[panels["macd"] * 2], [row["close"] for row in rows])
    if "institutional" in panels:
        _draw_institutional_bars(axes[panels["institutional"] * 2], rows, institutional)

    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", dpi=110, bbox_inches="tight")
    import matplotlib.pyplot as plt

    plt.close(fig)
    return buffer.getvalue()
