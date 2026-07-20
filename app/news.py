"""個股媒體新聞：Google News RSS（繁中台灣版），與官方重大訊息（今日資訊）互補。"""
import logging
import xml.etree.ElementTree as ET
from datetime import timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote

import httpx

logger = logging.getLogger(__name__)

_RSS_URL = "https://news.google.com/rss/search?q={query}&hl=zh-TW&gl=TW&ceid=TW:zh-Hant"
_HEADERS = {"User-Agent": "Mozilla/5.0"}
MAX_NEWS_ITEMS = 10
_TAIPEI_TZ = timezone(timedelta(hours=8))
_TITLE_MAX = 60


async def fetch_stock_news(http: httpx.AsyncClient, query: str, limit: int = MAX_NEWS_ITEMS) -> list[dict]:
    """Google News RSS → [{title, source, link, published}]；失敗回空列表。"""
    try:
        response = await http.get(_RSS_URL.format(query=quote(query)), headers=_HEADERS, timeout=15)
        response.raise_for_status()
        root = ET.fromstring(response.text)
    except Exception:
        logger.warning("Google News 抓取失敗 query=%s", query, exc_info=True)
        return []
    items = []
    for node in root.findall(".//item")[:limit]:
        title = (node.findtext("title") or "").strip()
        if not title:
            continue
        source = node.find("source")
        published = None
        if raw_date := node.findtext("pubDate"):
            try:
                published = parsedate_to_datetime(raw_date).astimezone(_TAIPEI_TZ)
            except (TypeError, ValueError):
                published = None
        items.append({
            "title": title,
            "source": (source.text or "").strip() if source is not None else "",
            "link": (node.findtext("link") or "").strip(),
            "published": published,
        })
    return items


def format_stock_news(stock: dict, items: list[dict]) -> str:
    """新聞清單 → LINE 文字：只放標題與連結，保持乾淨。"""
    if not items:
        return f"📰 {stock['name']}（{stock['stock_no']}）暫時找不到相關新聞。"
    lines = [f"📰 {stock['name']}（{stock['stock_no']}）相關新聞"]
    for i, item in enumerate(items, start=1):
        title = item["title"] if len(item["title"]) <= _TITLE_MAX else item["title"][:_TITLE_MAX] + "…"
        lines.append(f"{i}. {title}")
        if item["link"]:
            lines.append(item["link"])
    return "\n".join(lines)
