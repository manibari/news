"""
網頁爬蟲收集器
"""

import logging
from datetime import datetime
from typing import List, Optional

import requests
from bs4 import BeautifulSoup

from src.database.models import News
from src.utils.helpers import clean_text

from .base import BaseCollector

logger = logging.getLogger(__name__)


class WebScraper(BaseCollector):
    """網頁爬蟲新聞收集器"""

    def __init__(self, targets: List[dict], headers: Optional[dict] = None):
        """
        初始化網頁爬蟲

        Args:
            targets: 爬蟲目標設定列表
            headers: HTTP 請求標頭
        """
        super().__init__(name="Web Scraper", source_type="scraper")
        self.targets = targets
        self.headers = headers or {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }

    def collect(self) -> List[News]:
        """
        收集所有目標網站的新聞

        Returns:
            News 物件列表
        """
        all_news = []

        for target in self.targets:
            try:
                news_items = self._scrape_target(target)
                all_news.extend(news_items)
                logger.info(
                    f"[Scraper] {target['name']}: 收集到 {len(news_items)} 則新聞"
                )
            except Exception as e:
                logger.error(f"[Scraper] {target['name']} 爬取失敗: {e}")

        return all_news

    def _scrape_target(self, target: dict) -> List[News]:
        """
        爬取單一目標網站

        Args:
            target: 目標設定

        Returns:
            News 物件列表
        """
        name = target["name"]

        if "yahoo" in name.lower():
            return self._scrape_yahoo_finance(target)

        # 預設使用通用爬蟲
        return self._scrape_generic(target)

    def _scrape_yahoo_finance(self, target: dict) -> List[News]:
        """
        爬取 Yahoo Finance 新聞

        Args:
            target: 目標設定

        Returns:
            News 物件列表
        """
        response = requests.get(
            target["url"],
            headers=self.headers,
            timeout=30
        )
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")
        news_list = []

        # Yahoo Finance 新聞列表選擇器
        # 尋找新聞連結
        articles = soup.select('a[href*="/news/"]')

        seen_urls = set()

        for article in articles:
            try:
                href = article.get("href", "")
                if not href:
                    continue

                # 建立完整 URL
                if href.startswith("/"):
                    url = f"https://finance.yahoo.com{href}"
                elif href.startswith("http"):
                    url = href
                else:
                    continue

                # 去重
                if url in seen_urls:
                    continue
                seen_urls.add(url)

                # 取得標題
                title = clean_text(article.get_text())
                if not title or len(title) < 10:
                    continue

                news = News(
                    title=title,
                    content=None,
                    url=url,
                    source=target["name"],
                    category=target.get("category", "general"),
                    published_at=None,
                    collected_at=datetime.now(),
                    source_type=self.source_type,
                )
                news_list.append(news)

            except Exception as e:
                logger.debug(f"解析文章失敗: {e}")
                continue

        return news_list

    def _scrape_generic(self, target: dict) -> List[News]:
        """
        通用網頁爬蟲

        Args:
            target: 目標設定

        Returns:
            News 物件列表
        """
        response = requests.get(
            target["url"],
            headers=self.headers,
            timeout=30
        )
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")
        news_list = []

        # 嘗試尋找常見的新聞連結模式
        for link in soup.find_all("a", href=True):
            href = link.get("href", "")
            title = clean_text(link.get_text())

            # 過濾條件
            if not title or len(title) < 15:
                continue
            if not any(kw in href.lower() for kw in ["news", "article", "story"]):
                continue

            # 建立完整 URL
            if href.startswith("/"):
                base_url = "/".join(target["url"].split("/")[:3])
                url = f"{base_url}{href}"
            elif href.startswith("http"):
                url = href
            else:
                continue

            news = News(
                title=title,
                content=None,
                url=url,
                source=target["name"],
                category=target.get("category", "general"),
                published_at=None,
                collected_at=datetime.now(),
                source_type=self.source_type,
            )
            news_list.append(news)

        return news_list
