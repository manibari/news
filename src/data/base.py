"""
資料存取抽象介面

定義所有資料操作的標準介面，
具體實作由 SupabaseClient 或 SQLiteClient 提供
"""

from abc import ABC, abstractmethod
from datetime import date
from typing import List, Dict, Optional, Any


class DataClient(ABC):
    """資料存取抽象基類"""

    # ==================== 新聞 ====================

    @abstractmethod
    def get_news(
        self,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        source: Optional[str] = None,
        category: Optional[str] = None,
        limit: int = 100,
        offset: int = 0
    ) -> List[Dict]:
        """取得新聞列表"""
        pass

    @abstractmethod
    def get_news_count(
        self,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None
    ) -> int:
        """取得新聞總數"""
        pass

    @abstractmethod
    def get_news_sources(self) -> List[str]:
        """取得所有新聞來源"""
        pass

    @abstractmethod
    def get_news_categories(self) -> List[str]:
        """取得所有新聞類別"""
        pass

    @abstractmethod
    def search_news(self, keyword: str, limit: int = 50) -> List[Dict]:
        """搜尋新聞"""
        pass

    # ==================== 股票清單 ====================

    @abstractmethod
    def get_watchlist(
        self,
        market: Optional[str] = None,
        active_only: bool = True
    ) -> List[Dict]:
        """取得追蹤清單"""
        pass

    @abstractmethod
    def get_symbols(self, market: Optional[str] = None) -> List[str]:
        """取得所有股票代碼"""
        pass

    @abstractmethod
    def add_to_watchlist(
        self,
        symbol: str,
        name: Optional[str] = None,
        market: Optional[str] = None,
        sector: Optional[str] = None,
        industry: Optional[str] = None
    ) -> bool:
        """新增股票到追蹤清單"""
        pass

    # ==================== 價格數據 ====================

    @abstractmethod
    def get_daily_prices(
        self,
        symbol: str,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        limit: Optional[int] = None
    ) -> List[Dict]:
        """取得每日價格"""
        pass

    @abstractmethod
    def get_latest_price(self, symbol: str) -> Optional[Dict]:
        """取得最新價格"""
        pass

    @abstractmethod
    def get_price_stats(self) -> Dict[str, Any]:
        """取得價格統計"""
        pass

    # ==================== 總經數據 ====================

    @abstractmethod
    def get_macro_data(
        self,
        series_id: str,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        limit: Optional[int] = None
    ) -> List[Dict]:
        """取得總經數據"""
        pass

    @abstractmethod
    def get_macro_indicators(self, active_only: bool = True) -> List[Dict]:
        """取得總經指標清單"""
        pass

    @abstractmethod
    def get_latest_cycle(self) -> Optional[Dict]:
        """取得最新市場週期"""
        pass

    # ==================== 統計 ====================

    @abstractmethod
    def get_stats(self) -> Dict[str, Any]:
        """取得資料庫統計"""
        pass

    # ==================== 新聞寫入 ====================

    @abstractmethod
    def insert_news(self, news: Dict) -> bool:
        """
        插入單筆新聞

        Args:
            news: 新聞資料字典，包含:
                - title: 標題 (必填)
                - content: 內容
                - url: 網址 (用於去重)
                - source: 來源
                - category: 類別
                - published_at: 發布時間
                - source_type: 來源類型

        Returns:
            bool: 是否插入成功
        """
        pass

    @abstractmethod
    def insert_news_bulk(self, news_list: List[Dict]) -> int:
        """
        批量插入新聞

        Args:
            news_list: 新聞資料列表

        Returns:
            int: 成功插入的數量
        """
        pass

    # ==================== 股票清單寫入 ====================

    @abstractmethod
    def update_watchlist_status(self, symbol: str, is_active: bool) -> bool:
        """更新追蹤清單狀態"""
        pass

    # ==================== 價格數據寫入 ====================

    @abstractmethod
    def insert_daily_price(self, data: Dict) -> bool:
        """
        插入單筆每日價格

        Args:
            data: 價格資料字典，包含:
                - symbol: 股票代碼 (必填)
                - date: 日期 (必填)
                - open, high, low, close: 價格
                - adj_close: 調整後收盤價
                - volume: 成交量

        Returns:
            bool: 是否插入成功
        """
        pass

    @abstractmethod
    def insert_daily_prices_bulk(self, data_list: List[Dict]) -> int:
        """
        批量插入每日價格 (支援 upsert)

        Args:
            data_list: 價格資料列表

        Returns:
            int: 成功插入/更新的數量
        """
        pass

    @abstractmethod
    def insert_fundamentals(self, symbol: str, data: Dict) -> bool:
        """
        插入基本面數據

        Args:
            symbol: 股票代碼
            data: 基本面數據字典

        Returns:
            bool: 是否插入成功
        """
        pass

    # ==================== 總經數據寫入 ====================

    @abstractmethod
    def insert_macro_indicator(self, indicator: Dict) -> bool:
        """
        插入總經指標定義

        Args:
            indicator: 指標定義，包含:
                - series_id: FRED 系列 ID (必填)
                - name: 指標名稱 (必填)
                - frequency: 更新頻率
                - category: 分類

        Returns:
            bool: 是否插入成功
        """
        pass

    @abstractmethod
    def insert_macro_data(self, series_id: str, data: Dict) -> bool:
        """
        插入單筆總經數據

        Args:
            series_id: FRED 系列 ID
            data: 數據字典，包含:
                - date: 日期 (必填)
                - value: 數值 (必填)
                - change_pct: 變化百分比

        Returns:
            bool: 是否插入成功
        """
        pass

    @abstractmethod
    def insert_macro_data_bulk(self, series_id: str, data_list: List[Dict]) -> int:
        """
        批量插入總經數據

        Args:
            series_id: FRED 系列 ID
            data_list: 數據列表

        Returns:
            int: 成功插入的數量
        """
        pass

    @abstractmethod
    def insert_market_cycle(self, cycle_data: Dict) -> bool:
        """
        插入市場週期記錄

        Args:
            cycle_data: 週期資料，包含:
                - date: 日期 (必填)
                - phase: 週期階段 (必填)
                - score: 評分 (必填)
                - confidence: 信心度
                - signals: 訊號詳情 (JSON)
                - recommended_strategy: 建議策略

        Returns:
            bool: 是否插入成功
        """
        pass
