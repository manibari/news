"""
PTT Stock 版爬蟲收集器
"""

import logging
import re
import time
from datetime import datetime
from typing import List, Optional

import requests
from bs4 import BeautifulSoup

from src.database.models import News
from src.utils.helpers import clean_text

from .base import BaseCollector

logger = logging.getLogger(__name__)


class PTTCollector(BaseCollector):
    """PTT Stock 版新聞收集器"""

    BASE_URL = "https://www.ptt.cc/bbs/Stock/index.html"
    BOARD_URL = "https://www.ptt.cc/bbs/Stock"

    def __init__(self, pages: int = 5):
        """
        初始化 PTT 收集器

        Args:
            pages: 要爬取的頁數（每頁約 20 篇文章）
        """
        super().__init__(name="PTT Stock", source_type="ptt")
        self.pages = pages
        self.session = requests.Session()
        # PTT 需要設定 cookies 來通過年齡驗證
        self.session.cookies.set("over18", "1")
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Cache-Control": "max-age=0",
        }
        # 用於追蹤歷史爬取時的年份
        self._current_year = datetime.now().year
        self._last_month = datetime.now().month

    def collect(self) -> List[News]:
        """
        收集 PTT Stock 版文章

        Returns:
            News 物件列表
        """
        all_news = []
        current_url = self.BASE_URL

        for page_num in range(self.pages):
            try:
                news_items, prev_url = self._parse_page(current_url)
                all_news.extend(news_items)
                logger.info(f"[PTT] 第 {page_num + 1} 頁: 收集到 {len(news_items)} 則文章")

                if not prev_url:
                    break

                current_url = prev_url
                time.sleep(0.5)  # 避免請求過快

            except Exception as e:
                logger.error(f"[PTT] 第 {page_num + 1} 頁爬取失敗: {e}")
                break

        return all_news

    def _parse_page(self, url: str, track_year: bool = False) -> tuple:
        """
        解析單一頁面

        Args:
            url: 頁面 URL
            track_year: 是否追蹤年份變化（用於歷史爬取）

        Returns:
            (News 列表, 上一頁 URL)
        """
        # 重試機制
        max_retries = 3
        for attempt in range(max_retries):
            try:
                response = self.session.get(url, headers=self.headers, timeout=30)
                response.raise_for_status()
                break
            except Exception as e:
                if attempt < max_retries - 1:
                    time.sleep(2)
                    continue
                raise e

        soup = BeautifulSoup(response.text, "html.parser")
        news_list = []

        # 找到所有文章列表
        articles = soup.select("div.r-ent")

        for article in articles:
            news = self._parse_article(article, track_year=track_year)
            if news:
                news_list.append(news)

        # 找到上一頁連結
        prev_url = None
        paging = soup.select("div.btn-group-paging a")
        for link in paging:
            if "上頁" in link.text:
                prev_url = "https://www.ptt.cc" + link.get("href", "")
                break

        return news_list, prev_url

    def _parse_article(self, article_div, track_year: bool = False) -> Optional[News]:
        """
        解析單一文章

        Args:
            article_div: BeautifulSoup 文章 div 元素
            track_year: 是否追蹤年份變化（用於歷史爬取）

        Returns:
            News 物件，解析失敗則回傳 None
        """
        try:
            # 取得標題和連結
            title_elem = article_div.select_one("div.title a")
            if not title_elem:
                return None

            title = clean_text(title_elem.text)
            href = title_elem.get("href", "")

            if not title or not href:
                return None

            url = f"https://www.ptt.cc{href}"

            # 取得推文數
            push_elem = article_div.select_one("div.nrec span")
            push_count = push_elem.text if push_elem else "0"

            # 取得作者
            author_elem = article_div.select_one("div.meta div.author")
            author = author_elem.text.strip() if author_elem else ""

            # 從 URL 解析日期（更準確）
            # PTT URL 格式: /bbs/Stock/M.1706456789.A.xxx.html
            # M. 後面的數字是 Unix timestamp
            published_at = self._parse_date_from_url(href)

            # 如果 URL 解析失敗，fallback 到列表頁日期
            if not published_at:
                date_elem = article_div.select_one("div.meta div.date")
                date_str = date_elem.text.strip() if date_elem else ""
                published_at = self._parse_ptt_date(date_str, track_year=track_year)

            # 判斷文章分類
            category = self._categorize_title(title)

            # 建立摘要（包含推文數和作者）
            content = f"[{push_count}推] 作者: {author}"

            return News(
                title=title,
                content=content,
                url=url,
                source="PTT Stock",
                category=category,
                published_at=published_at,
                collected_at=datetime.now(),
                source_type=self.source_type,
            )

        except Exception as e:
            logger.debug(f"解析文章失敗: {e}")
            return None

    def _parse_date_from_url(self, href: str) -> Optional[datetime]:
        """
        從 PTT 文章 URL 解析日期

        Args:
            href: 文章連結 (如 /bbs/Stock/M.1706456789.A.xxx.html)

        Returns:
            datetime 物件
        """
        try:
            # 提取 Unix timestamp
            # 格式: /bbs/Stock/M.1706456789.A.xxx.html
            match = re.search(r'/M\.(\d+)\.', href)
            if match:
                timestamp = int(match.group(1))
                return datetime.fromtimestamp(timestamp)
            return None
        except Exception:
            return None

    def _parse_ptt_date(self, date_str: str, track_year: bool = False) -> Optional[datetime]:
        """
        解析 PTT 日期格式

        Args:
            date_str: PTT 日期字串 (M/DD)
            track_year: 是否追蹤年份變化（用於歷史爬取）

        Returns:
            datetime 物件
        """
        if not date_str:
            return None

        try:
            # PTT 日期格式: "1/28" 或 "12/31"
            month, day = date_str.strip().split("/")
            month = int(month)
            day = int(day)

            if track_year:
                # 歷史爬取模式：追蹤月份變化來判斷年份
                # 當從小月份跳到大月份（差距超過6個月），代表跨年了
                # 例如: 1月 -> 12月（差距11），或 2月 -> 11月（差距9）
                month_diff = month - self._last_month
                if month_diff > 6:  # 月份往後跳了超過6個月，代表跨到前一年
                    self._current_year -= 1
                self._last_month = month
                year = self._current_year
            else:
                # 一般模式：只用當前年份判斷
                year = datetime.now().year
                current_month = datetime.now().month
                if month > current_month:
                    year -= 1

            return datetime(year, month, day)
        except Exception:
            return None

    def reset_year_tracking(self):
        """重置年份追蹤（開始新的歷史爬取前呼叫）"""
        self._current_year = datetime.now().year
        self._last_month = datetime.now().month

    def _categorize_title(self, title: str) -> str:
        """
        根據標題分類文章

        Args:
            title: 文章標題

        Returns:
            分類名稱
        """
        # PTT 常見標籤
        if "[公告]" in title or "公告" in title:
            return "公告"
        elif "[標的]" in title or "標的" in title:
            return "標的"
        elif "[請益]" in title or "請益" in title:
            return "請益"
        elif "[心得]" in title or "心得" in title:
            return "心得"
        elif "[新聞]" in title or "新聞" in title:
            return "新聞"
        elif "[閒聊]" in title or "閒聊" in title:
            return "閒聊"
        elif "[情報]" in title or "情報" in title:
            return "情報"
        elif "Re:" in title:
            return "回覆"
        else:
            return "其他"


def collect_ptt_content(url: str, session: requests.Session, headers: dict) -> Optional[str]:
    """
    爬取單篇文章內容（進階功能）

    Args:
        url: 文章 URL
        session: requests Session
        headers: HTTP headers

    Returns:
        文章內容
    """
    try:
        response = session.get(url, headers=headers, timeout=30)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")

        # 找到主要內容
        main_content = soup.select_one("div#main-content")
        if not main_content:
            return None

        # 移除 meta 資訊
        for meta in main_content.select("div.article-metaline"):
            meta.decompose()
        for meta in main_content.select("div.article-metaline-right"):
            meta.decompose()

        # 移除推文
        for push in main_content.select("div.push"):
            push.decompose()

        content = clean_text(main_content.get_text())

        # 截取前 500 字
        if content and len(content) > 500:
            content = content[:500] + "..."

        return content

    except Exception as e:
        logger.debug(f"爬取內容失敗: {e}")
        return None
