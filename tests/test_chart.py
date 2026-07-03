from app.chart import ChartStore, render_kline_png

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _rows(days=40):
    rows = []
    for i in range(days):
        close = 100.0 + i
        rows.append(
            {
                "trade_date": f"2026-05-{i % 28 + 1:02d}" if i < 28 else f"2026-06-{i - 27:02d}",
                "close": close,
                "open": close - 1,
                "high": close + 2,
                "low": close - 2,
                "volume": 1000 + i,
            }
        )
    return rows


def test_render_kline_png_produces_png():
    png = render_kline_png(_rows(), "2330 台積電")
    assert png[:8] == PNG_MAGIC
    assert len(png) > 10_000


def test_render_tolerates_missing_open_high_low():
    rows = _rows(10)
    rows[3]["open"] = None
    rows[4]["high"] = None
    rows[5]["low"] = None
    png = render_kline_png(rows, "test")
    assert png[:8] == PNG_MAGIC


def test_chart_store_ttl_and_expiry():
    clock = {"now": 0.0}
    store = ChartStore(ttl_seconds=900, clock=lambda: clock["now"])
    chart_id = store.put(b"fake-png")
    assert store.get(chart_id) == b"fake-png"
    clock["now"] = 901
    assert store.get(chart_id) is None
