"""
資料庫連接與操作模組
"""

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Generator, List, Optional

from .models import News


class Database:
    """SQLite 資料庫管理類別"""

    def __init__(self, db_path: str = "news.db"):
        """
        初始化資料庫連接

        Args:
            db_path: 資料庫檔案路徑
        """
        self.db_path = Path(db_path)
        self._init_db()

    @contextmanager
    def _get_connection(self) -> Generator[sqlite3.Connection, None, None]:
        """取得資料庫連接的 context manager"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_db(self) -> None:
        """初始化資料庫，建立資料表"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS news (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    content TEXT,
                    url TEXT UNIQUE,
                    source TEXT,
                    category TEXT,
                    published_at DATETIME,
                    collected_at DATETIME,
                    source_type TEXT
                )
            """)

            # 建立索引以加速查詢
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_news_source
                ON news(source)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_news_category
                ON news(category)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_news_published_at
                ON news(published_at)
            """)

    def insert_news(self, news: News) -> Optional[int]:
        """
        插入新聞資料

        Args:
            news: News 物件

        Returns:
            插入的 id，若已存在則回傳 None
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute("""
                    INSERT INTO news (title, content, url, source, category,
                                     published_at, collected_at, source_type)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    news.title,
                    news.content,
                    news.url,
                    news.source,
                    news.category,
                    news.published_at,
                    news.collected_at or datetime.now(),
                    news.source_type,
                ))
                return cursor.lastrowid
            except sqlite3.IntegrityError:
                # URL 重複，跳過
                return None

    def insert_many(self, news_list: List[News]) -> int:
        """
        批量插入新聞資料

        Args:
            news_list: News 物件列表

        Returns:
            成功插入的筆數
        """
        inserted = 0
        for news in news_list:
            if self.insert_news(news) is not None:
                inserted += 1
        return inserted

    def url_exists(self, url: str) -> bool:
        """
        檢查 URL 是否已存在

        Args:
            url: 新聞連結

        Returns:
            是否存在
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM news WHERE url = ?", (url,))
            return cursor.fetchone() is not None

    def get_news_count(self) -> int:
        """取得新聞總數"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM news")
            return cursor.fetchone()[0]

    def get_news_by_source_type(self) -> dict:
        """依來源類型統計新聞數量"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT source_type, COUNT(*) as count
                FROM news
                GROUP BY source_type
            """)
            return {row["source_type"]: row["count"] for row in cursor.fetchall()}

    def get_recent_news(self, limit: int = 10) -> List[News]:
        """
        取得最近的新聞

        Args:
            limit: 回傳筆數

        Returns:
            News 物件列表
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM news
                ORDER BY collected_at DESC
                LIMIT ?
            """, (limit,))

            return [
                News(
                    id=row["id"],
                    title=row["title"],
                    content=row["content"],
                    url=row["url"],
                    source=row["source"],
                    category=row["category"],
                    published_at=row["published_at"],
                    collected_at=row["collected_at"],
                    source_type=row["source_type"],
                )
                for row in cursor.fetchall()
            ]
