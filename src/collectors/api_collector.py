"""
新聞 API 收集器 (NewsAPI.org)
"""

import logging
from datetime import datetime, timedelta
from typing import List, Optional

import requests

from src.database.models import News
from src.utils.helpers import clean_text, parse_date

from .base import BaseCollector

logger = logging.getLogger(__name__)


class NewsAPICollector(BaseCollector):
    """NewsAPI.org 新聞收集器"""

    BASE_URL = "https://newsapi.org/v2/everything"

    def __init__(self, api_key: str, keywords: List[str], category: str = "macro"):
        """
        初始化 NewsAPI 收集器

        Args:
            api_key: NewsAPI API Key
            keywords: 搜尋關鍵字列表
            category: 新聞分類
        """
        super().__init__(name="NewsAPI", source_type="api")
        self.api_key = api_key
        self.keywords = keywords
        self.category = category

    def collect(self) -> List[News]:
        """
        收集新聞 API 的新聞

        Returns:
            News 物件列表
        """
        if not self.api_key or self.api_key == "your-api-key-here":
            logger.warning("[API] NewsAPI key 未設定，跳過 API 收集")
            return []

        all_news = []

        for keyword in self.keywords:
            try:
                news_items = self._search_news(keyword)
                all_news.extend(news_items)
                logger.info(f"[API] 關鍵字 '{keyword}': 收集到 {len(news_items)} 則新聞")
            except Exception as e:
                logger.error(f"[API] 關鍵字 '{keyword}' 搜尋失敗: {e}")

        # 去重（依 URL）
        seen_urls = set()
        unique_news = []
        for news in all_news:
            if news.url not in seen_urls:
                seen_urls.add(news.url)
                unique_news.append(news)

        return unique_news

    def _search_news(self, keyword: str) -> List[News]:
        """
        搜尋特定關鍵字的新聞

        Args:
            keyword: 搜尋關鍵字

        Returns:
            News 物件列表
        """
        # 搜尋過去 7 天的新聞
        from_date = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")

        params = {
            "q": keyword,
            "from": from_date,
            "sortBy": "publishedAt",
            "language": "en",
            "pageSize": 20,  # 每個關鍵字最多 20 則
            "apiKey": self.api_key,
        }

        response = requests.get(self.BASE_URL, params=params, timeout=30)
        response.raise_for_status()

        data = response.json()

        if data.get("status") != "ok":
            raise ValueError(f"API 回應錯誤: {data.get('message', 'Unknown error')}")

        news_list = []
        for article in data.get("articles", []):
            news = self._parse_article(article)
            if news:
                news_list.append(news)

        return news_list

    def _parse_article(self, article: dict) -> Optional[News]:
        """
        解析單一文章

        Args:
            article: API 回應的文章資料

        Returns:
            News 物件，解析失敗則回傳 None
        """
        try:
            title = clean_text(article.get("title", ""))
            url = article.get("url", "")

            if not title or not url:
                return None

            # 取得來源名稱
            source = "NewsAPI"
            if article.get("source") and article["source"].get("name"):
                source = article["source"]["name"]

            return News(
                title=title,
                content=clean_text(article.get("description")),
                url=url,
                source=source,
                category=self.category,
                published_at=parse_date(article.get("publishedAt")),
                collected_at=datetime.now(),
                source_type=self.source_type,
            )
        except Exception as e:
            logger.debug(f"解析文章失敗: {e}")
            return None
