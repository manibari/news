"""
RSS Feed 收集器
"""

import logging
from datetime import datetime
from typing import List, Optional

import feedparser

from src.database.models import News
from src.utils.helpers import clean_text, parse_date

from .base import BaseCollector

logger = logging.getLogger(__name__)


class RSSCollector(BaseCollector):
    """RSS Feed 新聞收集器"""

    def __init__(self, feeds: List[dict]):
        """
        初始化 RSS 收集器

        Args:
            feeds: RSS feed 設定列表，每個包含 name, url, category
        """
        super().__init__(name="RSS Collector", source_type="rss")
        self.feeds = feeds

    def collect(self) -> List[News]:
        """
        收集所有 RSS feed 的新聞

        Returns:
            News 物件列表
        """
        all_news = []

        for feed_config in self.feeds:
            try:
                news_items = self._parse_feed(feed_config)
                all_news.extend(news_items)
                logger.info(
                    f"[RSS] {feed_config['name']}: 收集到 {len(news_items)} 則新聞"
                )
            except Exception as e:
                logger.error(f"[RSS] {feed_config['name']} 收集失敗: {e}")

        return all_news

    def _parse_feed(self, feed_config: dict) -> List[News]:
        """
        解析單一 RSS feed

        Args:
            feed_config: feed 設定

        Returns:
            News 物件列表
        """
        feed = feedparser.parse(feed_config["url"])
        news_list = []

        for entry in feed.entries:
            news = self._parse_entry(entry, feed_config)
            if news:
                news_list.append(news)

        return news_list

    def _parse_entry(self, entry: dict, feed_config: dict) -> Optional[News]:
        """
        解析單一 feed entry

        Args:
            entry: feedparser entry
            feed_config: feed 設定

        Returns:
            News 物件，解析失敗則回傳 None
        """
        try:
            # 取得標題
            title = clean_text(entry.get("title", ""))
            if not title:
                return None

            # 取得連結
            url = entry.get("link", "")
            if not url:
                return None

            # 取得內容摘要
            content = None
            if "summary" in entry:
                content = clean_text(entry.summary)
            elif "description" in entry:
                content = clean_text(entry.description)

            # 取得發布時間
            published_at = None
            if "published" in entry:
                published_at = parse_date(entry.published)
            elif "updated" in entry:
                published_at = parse_date(entry.updated)

            return News(
                title=title,
                content=content,
                url=url,
                source=feed_config["name"],
                category=feed_config.get("category", "general"),
                published_at=published_at,
                collected_at=datetime.now(),
                source_type=self.source_type,
            )
        except Exception as e:
            logger.debug(f"解析 entry 失敗: {e}")
            return None
