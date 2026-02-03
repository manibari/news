"""
Supabase 資料客戶端實作
"""

from datetime import date
from typing import List, Dict, Optional, Any

from supabase import create_client, Client

from .base import DataClient


class SupabaseClient(DataClient):
    """Supabase 資料存取實作"""

    def __init__(self):
        from config.supabase_config import SUPABASE_URL, SUPABASE_KEY
        self._client: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

    @property
    def client(self) -> Client:
        return self._client

    # ==================== 新聞 ====================

    def get_news(
        self,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        source: Optional[str] = None,
        category: Optional[str] = None,
        limit: int = 100,
        offset: int = 0
    ) -> List[Dict]:
        query = self._client.table("news").select("*")

        if start_date:
            query = query.gte("published_at", start_date.isoformat())
        if end_date:
            query = query.lte("published_at", f"{end_date.isoformat()}T23:59:59")
        if source:
            query = query.eq("source", source)
        if category:
            query = query.eq("category", category)

        query = query.order("published_at", desc=True).range(offset, offset + limit - 1)
        result = query.execute()
        return result.data

    def get_news_count(
        self,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None
    ) -> int:
        query = self._client.table("news").select("*", count="exact")

        if start_date:
            query = query.gte("published_at", start_date.isoformat())
        if end_date:
            query = query.lte("published_at", f"{end_date.isoformat()}T23:59:59")

        result = query.limit(1).execute()
        return result.count or 0

    def get_news_sources(self) -> List[str]:
        result = self._client.table("news").select("source").execute()
        sources = set(row["source"] for row in result.data if row.get("source"))
        return sorted(sources)

    def get_news_categories(self) -> List[str]:
        result = self._client.table("news").select("category").execute()
        categories = set(row["category"] for row in result.data if row.get("category"))
        return sorted(categories)

    def search_news(self, keyword: str, limit: int = 50) -> List[Dict]:
        result = (
            self._client.table("news")
            .select("*")
            .ilike("title", f"%{keyword}%")
            .order("published_at", desc=True)
            .limit(limit)
            .execute()
        )
        return result.data

    # ==================== 股票清單 ====================

    def get_watchlist(
        self,
        market: Optional[str] = None,
        active_only: bool = True
    ) -> List[Dict]:
        query = self._client.table("watchlist").select("*")

        if active_only:
            query = query.eq("is_active", True)
        if market:
            query = query.eq("market", market)

        result = query.order("market").order("symbol").execute()
        return result.data

    def get_symbols(self, market: Optional[str] = None) -> List[str]:
        watchlist = self.get_watchlist(market=market)
        return [item["symbol"] for item in watchlist]

    def add_to_watchlist(
        self,
        symbol: str,
        name: Optional[str] = None,
        market: Optional[str] = None,
        sector: Optional[str] = None,
        industry: Optional[str] = None
    ) -> bool:
        data = {
            "symbol": symbol.upper(),
            "name": name,
            "market": market,
            "sector": sector,
            "industry": industry,
            "is_active": True
        }
        try:
            self._client.table("watchlist").upsert(data, on_conflict="symbol").execute()
            return True
        except Exception:
            return False

    # ==================== 價格數據 ====================

    def get_daily_prices(
        self,
        symbol: str,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        limit: Optional[int] = None
    ) -> List[Dict]:
        query = self._client.table("daily_prices").select("*").eq("symbol", symbol.upper())

        if start_date:
            query = query.gte("date", start_date.isoformat())
        if end_date:
            query = query.lte("date", end_date.isoformat())

        query = query.order("date", desc=True)

        if limit:
            query = query.limit(limit)

        result = query.execute()
        return result.data

    def get_latest_price(self, symbol: str) -> Optional[Dict]:
        prices = self.get_daily_prices(symbol, limit=1)
        return prices[0] if prices else None

    def get_price_stats(self) -> Dict[str, Any]:
        # 總筆數
        count_result = self._client.table("daily_prices").select("*", count="exact").limit(1).execute()

        # 日期範圍 - 取最早和最晚的記錄
        min_result = self._client.table("daily_prices").select("date").order("date").limit(1).execute()
        max_result = self._client.table("daily_prices").select("date").order("date", desc=True).limit(1).execute()

        return {
            "count": count_result.count or 0,
            "min_date": min_result.data[0]["date"] if min_result.data else None,
            "max_date": max_result.data[0]["date"] if max_result.data else None
        }

    # ==================== 總經數據 ====================

    def get_macro_data(
        self,
        series_id: str,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None
    ) -> List[Dict]:
        query = self._client.table("macro_data").select("*").eq("series_id", series_id)

        if start_date:
            query = query.gte("date", start_date.isoformat())
        if end_date:
            query = query.lte("date", end_date.isoformat())

        result = query.order("date", desc=True).execute()
        return result.data

    def get_macro_indicators(self, active_only: bool = True) -> List[Dict]:
        query = self._client.table("macro_indicators").select("*")

        if active_only:
            query = query.eq("is_active", True)

        result = query.execute()
        return result.data

    def get_latest_cycle(self) -> Optional[Dict]:
        result = (
            self._client.table("market_cycles")
            .select("*")
            .order("date", desc=True)
            .limit(1)
            .execute()
        )
        return result.data[0] if result.data else None

    # ==================== 統計 ====================

    def get_stats(self) -> Dict[str, Any]:
        # 各表筆數
        news_count = self._client.table("news").select("*", count="exact").limit(1).execute()
        watchlist_count = self._client.table("watchlist").select("*", count="exact").eq("is_active", True).limit(1).execute()
        prices_count = self._client.table("daily_prices").select("*", count="exact").limit(1).execute()

        # 按市場分類
        watchlist = self.get_watchlist()
        by_market = {}
        for item in watchlist:
            market = item.get("market") or "OTHER"
            by_market[market] = by_market.get(market, 0) + 1

        # 價格日期範圍
        price_stats = self.get_price_stats()

        return {
            "news_count": news_count.count or 0,
            "watchlist_count": watchlist_count.count or 0,
            "prices_count": prices_count.count or 0,
            "by_market": by_market,
            "date_range": {
                "min": price_stats["min_date"],
                "max": price_stats["max_date"]
            }
        }
