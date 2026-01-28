"""
資料模型定義
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class News:
    """新聞資料模型"""

    title: str
    url: str
    source: str
    category: str
    source_type: str  # rss, api, scraper
    content: Optional[str] = None
    published_at: Optional[datetime] = None
    collected_at: Optional[datetime] = None
    id: Optional[int] = None

    def to_dict(self) -> dict:
        """轉換為字典"""
        return {
            "id": self.id,
            "title": self.title,
            "content": self.content,
            "url": self.url,
            "source": self.source,
            "category": self.category,
            "published_at": self.published_at,
            "collected_at": self.collected_at,
            "source_type": self.source_type,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "News":
        """從字典建立實例"""
        return cls(
            id=data.get("id"),
            title=data["title"],
            content=data.get("content"),
            url=data["url"],
            source=data["source"],
            category=data["category"],
            published_at=data.get("published_at"),
            collected_at=data.get("collected_at"),
            source_type=data["source_type"],
        )
