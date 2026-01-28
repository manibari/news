"""
基礎收集器抽象類別
"""

from abc import ABC, abstractmethod
from typing import List

from src.database.models import News


class BaseCollector(ABC):
    """新聞收集器基礎類別"""

    def __init__(self, name: str, source_type: str):
        """
        初始化收集器

        Args:
            name: 收集器名稱
            source_type: 來源類型 (rss/api/scraper)
        """
        self.name = name
        self.source_type = source_type

    @abstractmethod
    def collect(self) -> List[News]:
        """
        收集新聞

        Returns:
            News 物件列表
        """
        pass

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(name={self.name})"
