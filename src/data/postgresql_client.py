"""
PostgreSQL 資料客戶端實作（地端 PostgreSQL）
"""

import os
import json
from contextlib import contextmanager
from datetime import date
from typing import List, Dict, Optional, Any, Generator

from .base import DataClient

# 嘗試載入 .env
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


class PostgreSQLClient(DataClient):
    """PostgreSQL 資料存取實作"""

    def __init__(
        self,
        host: Optional[str] = None,
        port: Optional[str] = None,
        database: Optional[str] = None,
        user: Optional[str] = None,
        password: Optional[str] = None
    ):
        """
        初始化 PostgreSQL 客戶端

        Args:
            可透過參數或環境變數設定連線資訊
        """
        self.config = {
            'host': host or os.getenv('DB_HOST', 'localhost'),
            'port': port or os.getenv('DB_PORT', '5432'),
            'database': database or os.getenv('DB_NAME', 'stock_analysis'),
            'user': user or os.getenv('DB_USER', 'postgres'),
            'password': password or os.getenv('DB_PASSWORD', ''),
        }
        self._conn = None

    @contextmanager
    def _get_conn(self) -> Generator:
        """取得資料庫連線"""
        import psycopg2
        from psycopg2.extras import RealDictCursor

        conn = psycopg2.connect(**self.config)
        try:
            yield conn, conn.cursor(cursor_factory=RealDictCursor)
        finally:
            conn.close()

    def _execute(self, query: str, params: tuple = None, fetch: bool = True) -> List[Dict]:
        """執行查詢"""
        with self._get_conn() as (conn, cursor):
            cursor.execute(query, params)
            if fetch:
                return [dict(row) for row in cursor.fetchall()]
            conn.commit()
            return []

    def _execute_one(self, query: str, params: tuple = None) -> Optional[Dict]:
        """執行查詢並取得單筆結果"""
        with self._get_conn() as (conn, cursor):
            cursor.execute(query, params)
            row = cursor.fetchone()
            return dict(row) if row else None

    def _execute_write(self, query: str, params: tuple = None) -> bool:
        """執行寫入操作"""
        try:
            with self._get_conn() as (conn, cursor):
                cursor.execute(query, params)
                conn.commit()
                return cursor.rowcount > 0
        except Exception:
            return False

    def _execute_many(self, query: str, params_list: List[tuple]) -> int:
        """批量執行寫入"""
        if not params_list:
            return 0
        try:
            with self._get_conn() as (conn, cursor):
                cursor.executemany(query, params_list)
                conn.commit()
                return cursor.rowcount
        except Exception:
            return 0

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
        query = "SELECT * FROM news WHERE 1=1"
        params = []

        if start_date:
            query += " AND published_at::date >= %s"
            params.append(start_date)
        if end_date:
            query += " AND published_at::date <= %s"
            params.append(end_date)
        if source:
            query += " AND source = %s"
            params.append(source)
        if category:
            query += " AND category = %s"
            params.append(category)

        query += " ORDER BY published_at DESC LIMIT %s OFFSET %s"
        params.extend([limit, offset])

        return self._execute(query, tuple(params))

    def get_news_count(
        self,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None
    ) -> int:
        query = "SELECT COUNT(*) as count FROM news WHERE 1=1"
        params = []

        if start_date:
            query += " AND published_at::date >= %s"
            params.append(start_date)
        if end_date:
            query += " AND published_at::date <= %s"
            params.append(end_date)

        result = self._execute_one(query, tuple(params))
        return result["count"] if result else 0

    def get_news_sources(self) -> List[str]:
        result = self._execute(
            "SELECT DISTINCT source FROM news WHERE source IS NOT NULL ORDER BY source"
        )
        return [r["source"] for r in result]

    def get_news_categories(self) -> List[str]:
        result = self._execute(
            "SELECT DISTINCT category FROM news WHERE category IS NOT NULL ORDER BY category"
        )
        return [r["category"] for r in result]

    def search_news(self, keyword: str, limit: int = 50) -> List[Dict]:
        return self._execute(
            "SELECT * FROM news WHERE title ILIKE %s ORDER BY published_at DESC LIMIT %s",
            (f"%{keyword}%", limit)
        )

    # ==================== 股票清單 ====================

    def get_watchlist(
        self,
        market: Optional[str] = None,
        active_only: bool = True
    ) -> List[Dict]:
        query = "SELECT * FROM watchlist WHERE 1=1"
        params = []

        if active_only:
            query += " AND is_active = TRUE"
        if market:
            query += " AND market = %s"
            params.append(market)

        query += " ORDER BY market, symbol"
        return self._execute(query, tuple(params) if params else None)

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
        return self._execute_write(
            """INSERT INTO watchlist (symbol, name, market, sector, industry)
               VALUES (%s, %s, %s, %s, %s)
               ON CONFLICT(symbol) DO UPDATE SET
                   name = COALESCE(EXCLUDED.name, watchlist.name),
                   market = COALESCE(EXCLUDED.market, watchlist.market),
                   sector = COALESCE(EXCLUDED.sector, watchlist.sector),
                   industry = COALESCE(EXCLUDED.industry, watchlist.industry),
                   updated_at = NOW()
            """,
            (symbol.upper(), name, market, sector, industry)
        )

    # ==================== 價格數據 ====================

    def get_daily_prices(
        self,
        symbol: str,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        limit: Optional[int] = None
    ) -> List[Dict]:
        query = "SELECT * FROM daily_prices WHERE symbol = %s"
        params = [symbol.upper()]

        if start_date:
            query += " AND date >= %s"
            params.append(start_date)
        if end_date:
            query += " AND date <= %s"
            params.append(end_date)

        query += " ORDER BY date DESC"

        if limit:
            query += " LIMIT %s"
            params.append(limit)

        return self._execute(query, tuple(params))

    def get_latest_price(self, symbol: str) -> Optional[Dict]:
        prices = self.get_daily_prices(symbol, limit=1)
        return prices[0] if prices else None

    def get_price_stats(self) -> Dict[str, Any]:
        result = self._execute_one(
            "SELECT COUNT(*) as count, MIN(date) as min_date, MAX(date) as max_date FROM daily_prices"
        )
        return {
            "count": result["count"] if result else 0,
            "min_date": str(result["min_date"]) if result and result["min_date"] else None,
            "max_date": str(result["max_date"]) if result and result["max_date"] else None
        }

    # ==================== 總經數據 ====================

    def get_macro_data(
        self,
        series_id: str,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        limit: Optional[int] = None
    ) -> List[Dict]:
        query = "SELECT * FROM macro_data WHERE series_id = %s"
        params = [series_id]

        if start_date:
            query += " AND date >= %s"
            params.append(start_date)
        if end_date:
            query += " AND date <= %s"
            params.append(end_date)

        query += " ORDER BY date DESC"

        if limit:
            query += " LIMIT %s"
            params.append(limit)

        return self._execute(query, tuple(params))

    def get_macro_indicators(self, active_only: bool = True) -> List[Dict]:
        query = "SELECT * FROM macro_indicators"
        if active_only:
            query += " WHERE is_active = TRUE"
        return self._execute(query)

    def get_latest_cycle(self) -> Optional[Dict]:
        return self._execute_one(
            "SELECT * FROM market_cycles ORDER BY date DESC LIMIT 1"
        )

    # ==================== 統計 ====================

    def get_stats(self) -> Dict[str, Any]:
        stats = {
            "news_count": 0,
            "watchlist_count": 0,
            "prices_count": 0,
            "by_market": {},
            "date_range": None
        }

        try:
            # 新聞統計
            result = self._execute_one("SELECT COUNT(*) as count FROM news")
            stats["news_count"] = result["count"] if result else 0

            # 股票統計
            result = self._execute_one("SELECT COUNT(*) as count FROM watchlist WHERE is_active = TRUE")
            stats["watchlist_count"] = result["count"] if result else 0

            result = self._execute_one("SELECT COUNT(*) as count FROM daily_prices")
            stats["prices_count"] = result["count"] if result else 0

            # 市場分佈
            market_stats = self._execute(
                "SELECT market, COUNT(*) as count FROM watchlist WHERE is_active = TRUE GROUP BY market"
            )
            stats["by_market"] = {r["market"]: r["count"] for r in market_stats if r["market"]}

            # 日期範圍
            result = self._execute_one("SELECT MIN(date) as min_date, MAX(date) as max_date FROM daily_prices")
            if result and result["min_date"]:
                stats["date_range"] = {
                    "min": str(result["min_date"]),
                    "max": str(result["max_date"])
                }
        except Exception:
            pass

        return stats

    # ==================== 新聞寫入 ====================

    def insert_news(self, news: Dict) -> bool:
        return self._execute_write(
            """INSERT INTO news (title, content, url, source, category, published_at, source_type)
               VALUES (%s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT(url) DO NOTHING
            """,
            (
                news.get("title"),
                news.get("content"),
                news.get("url"),
                news.get("source"),
                news.get("category"),
                news.get("published_at"),
                news.get("source_type")
            )
        )

    def insert_news_bulk(self, news_list: List[Dict]) -> int:
        if not news_list:
            return 0

        return self._execute_many(
            """INSERT INTO news (title, content, url, source, category, published_at, source_type)
               VALUES (%s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT(url) DO NOTHING
            """,
            [
                (
                    n.get("title"),
                    n.get("content"),
                    n.get("url"),
                    n.get("source"),
                    n.get("category"),
                    n.get("published_at"),
                    n.get("source_type")
                )
                for n in news_list
            ]
        )

    # ==================== 股票清單寫入 ====================

    def update_watchlist_status(self, symbol: str, is_active: bool) -> bool:
        return self._execute_write(
            "UPDATE watchlist SET is_active = %s, updated_at = NOW() WHERE symbol = %s",
            (is_active, symbol.upper())
        )

    # ==================== 價格數據寫入 ====================

    def insert_daily_price(self, data: Dict) -> bool:
        return self._execute_write(
            """INSERT INTO daily_prices (symbol, date, open, high, low, close, adj_close, volume)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT(symbol, date) DO UPDATE SET
                   open = EXCLUDED.open,
                   high = EXCLUDED.high,
                   low = EXCLUDED.low,
                   close = EXCLUDED.close,
                   adj_close = EXCLUDED.adj_close,
                   volume = EXCLUDED.volume
            """,
            (
                data.get("symbol", "").upper(),
                data.get("date"),
                data.get("open"),
                data.get("high"),
                data.get("low"),
                data.get("close"),
                data.get("adj_close"),
                data.get("volume")
            )
        )

    def insert_daily_prices_bulk(self, data_list: List[Dict]) -> int:
        if not data_list:
            return 0

        return self._execute_many(
            """INSERT INTO daily_prices (symbol, date, open, high, low, close, adj_close, volume)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT(symbol, date) DO UPDATE SET
                   open = EXCLUDED.open,
                   high = EXCLUDED.high,
                   low = EXCLUDED.low,
                   close = EXCLUDED.close,
                   adj_close = EXCLUDED.adj_close,
                   volume = EXCLUDED.volume
            """,
            [
                (
                    d.get("symbol", "").upper(),
                    d.get("date"),
                    d.get("open"),
                    d.get("high"),
                    d.get("low"),
                    d.get("close"),
                    d.get("adj_close"),
                    d.get("volume")
                )
                for d in data_list
            ]
        )

    def insert_fundamentals(self, symbol: str, data: Dict) -> bool:
        raw_data = data.get("raw_data")
        if raw_data and not isinstance(raw_data, str):
            raw_data = json.dumps(raw_data)

        return self._execute_write(
            """INSERT INTO fundamentals (
                   symbol, date, market_cap, enterprise_value, pe_ratio, forward_pe,
                   peg_ratio, pb_ratio, ps_ratio, dividend_yield, eps, revenue,
                   profit_margin, operating_margin, roe, roa, debt_to_equity,
                   current_ratio, quick_ratio, beta, fifty_two_week_high,
                   fifty_two_week_low, fifty_day_avg, two_hundred_day_avg,
                   avg_volume, shares_outstanding, float_shares, held_by_institutions,
                   short_ratio, raw_data
               )
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT(symbol, date) DO UPDATE SET
                   market_cap = EXCLUDED.market_cap,
                   pe_ratio = EXCLUDED.pe_ratio,
                   pb_ratio = EXCLUDED.pb_ratio,
                   eps = EXCLUDED.eps,
                   raw_data = EXCLUDED.raw_data
            """,
            (
                symbol.upper(),
                data.get("date"),
                data.get("market_cap"),
                data.get("enterprise_value"),
                data.get("pe_ratio"),
                data.get("forward_pe"),
                data.get("peg_ratio"),
                data.get("pb_ratio"),
                data.get("ps_ratio"),
                data.get("dividend_yield"),
                data.get("eps"),
                data.get("revenue"),
                data.get("profit_margin"),
                data.get("operating_margin"),
                data.get("roe"),
                data.get("roa"),
                data.get("debt_to_equity"),
                data.get("current_ratio"),
                data.get("quick_ratio"),
                data.get("beta"),
                data.get("fifty_two_week_high"),
                data.get("fifty_two_week_low"),
                data.get("fifty_day_avg"),
                data.get("two_hundred_day_avg"),
                data.get("avg_volume"),
                data.get("shares_outstanding"),
                data.get("float_shares"),
                data.get("held_by_institutions"),
                data.get("short_ratio"),
                raw_data
            )
        )

    # ==================== 總經數據寫入 ====================

    def insert_macro_indicator(self, indicator: Dict) -> bool:
        # 轉換 is_active 為 boolean (SQLite 使用 0/1)
        is_active = bool(indicator.get("is_active", True))

        return self._execute_write(
            """INSERT INTO macro_indicators (series_id, name, frequency, category, is_active)
               VALUES (%s, %s, %s, %s, %s)
               ON CONFLICT(series_id) DO UPDATE SET
                   name = EXCLUDED.name,
                   frequency = EXCLUDED.frequency,
                   category = EXCLUDED.category
            """,
            (
                indicator.get("series_id"),
                indicator.get("name"),
                indicator.get("frequency"),
                indicator.get("category"),
                is_active
            )
        )

    def insert_macro_data(self, series_id: str, data: Dict) -> bool:
        return self._execute_write(
            """INSERT INTO macro_data (series_id, date, value, change_pct)
               VALUES (%s, %s, %s, %s)
               ON CONFLICT(series_id, date) DO UPDATE SET
                   value = EXCLUDED.value,
                   change_pct = EXCLUDED.change_pct
            """,
            (
                series_id,
                data.get("date"),
                data.get("value"),
                data.get("change_pct")
            )
        )

    def insert_macro_data_bulk(self, series_id: str, data_list: List[Dict]) -> int:
        if not data_list:
            return 0

        return self._execute_many(
            """INSERT INTO macro_data (series_id, date, value, change_pct)
               VALUES (%s, %s, %s, %s)
               ON CONFLICT(series_id, date) DO UPDATE SET
                   value = EXCLUDED.value,
                   change_pct = EXCLUDED.change_pct
            """,
            [
                (
                    series_id,
                    d.get("date"),
                    d.get("value"),
                    d.get("change_pct")
                )
                for d in data_list
            ]
        )

    def insert_market_cycle(self, cycle_data: Dict) -> bool:
        signals = cycle_data.get("signals")
        if signals and not isinstance(signals, str):
            signals = json.dumps(signals)

        return self._execute_write(
            """INSERT INTO market_cycles (date, phase, score, confidence, signals, recommended_strategy)
               VALUES (%s, %s, %s, %s, %s, %s)
               ON CONFLICT(date) DO UPDATE SET
                   phase = EXCLUDED.phase,
                   score = EXCLUDED.score,
                   confidence = EXCLUDED.confidence,
                   signals = EXCLUDED.signals,
                   recommended_strategy = EXCLUDED.recommended_strategy
            """,
            (
                cycle_data.get("date"),
                cycle_data.get("phase"),
                cycle_data.get("score"),
                cycle_data.get("confidence"),
                signals,
                cycle_data.get("recommended_strategy")
            )
        )
