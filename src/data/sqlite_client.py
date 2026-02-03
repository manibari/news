"""
SQLite 資料客戶端實作（本地開發用）
"""

import sqlite3
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import List, Dict, Optional, Any, Generator

from .base import DataClient


class SQLiteClient(DataClient):
    """SQLite 資料存取實作"""

    def __init__(
        self,
        news_db: str = "news.db",
        finance_db: str = "finance.db",
        macro_db: str = "macro.db"
    ):
        self.news_db = Path(news_db)
        self.finance_db = Path(finance_db)
        self.macro_db = Path(macro_db)

    @contextmanager
    def _get_conn(self, db_path: Path, create_if_missing: bool = False) -> Generator[sqlite3.Connection, None, None]:
        """取得資料庫連線

        Args:
            db_path: 資料庫路徑
            create_if_missing: 若資料庫不存在是否自動建立
        """
        if not db_path.exists() and not create_if_missing:
            raise FileNotFoundError(f"資料庫不存在: {db_path}")

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def _rows_to_dicts(self, rows) -> List[Dict]:
        return [dict(row) for row in rows]

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
        with self._get_conn(self.news_db) as conn:
            query = "SELECT * FROM news WHERE 1=1"
            params = []

            if start_date:
                query += " AND date(published_at) >= ?"
                params.append(start_date.isoformat())
            if end_date:
                query += " AND date(published_at) <= ?"
                params.append(end_date.isoformat())
            if source:
                query += " AND source = ?"
                params.append(source)
            if category:
                query += " AND category = ?"
                params.append(category)

            query += " ORDER BY published_at DESC LIMIT ? OFFSET ?"
            params.extend([limit, offset])

            cursor = conn.execute(query, params)
            return self._rows_to_dicts(cursor.fetchall())

    def get_news_count(
        self,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None
    ) -> int:
        with self._get_conn(self.news_db) as conn:
            query = "SELECT COUNT(*) FROM news WHERE 1=1"
            params = []

            if start_date:
                query += " AND date(published_at) >= ?"
                params.append(start_date.isoformat())
            if end_date:
                query += " AND date(published_at) <= ?"
                params.append(end_date.isoformat())

            cursor = conn.execute(query, params)
            return cursor.fetchone()[0]

    def get_news_sources(self) -> List[str]:
        with self._get_conn(self.news_db) as conn:
            cursor = conn.execute("SELECT DISTINCT source FROM news WHERE source IS NOT NULL ORDER BY source")
            return [row[0] for row in cursor.fetchall()]

    def get_news_categories(self) -> List[str]:
        with self._get_conn(self.news_db) as conn:
            cursor = conn.execute("SELECT DISTINCT category FROM news WHERE category IS NOT NULL ORDER BY category")
            return [row[0] for row in cursor.fetchall()]

    def search_news(self, keyword: str, limit: int = 50) -> List[Dict]:
        with self._get_conn(self.news_db) as conn:
            cursor = conn.execute(
                "SELECT * FROM news WHERE title LIKE ? ORDER BY published_at DESC LIMIT ?",
                (f"%{keyword}%", limit)
            )
            return self._rows_to_dicts(cursor.fetchall())

    # ==================== 股票清單 ====================

    def get_watchlist(
        self,
        market: Optional[str] = None,
        active_only: bool = True
    ) -> List[Dict]:
        with self._get_conn(self.finance_db) as conn:
            query = "SELECT * FROM watchlist WHERE 1=1"
            params = []

            if active_only:
                query += " AND is_active = 1"
            if market:
                query += " AND market = ?"
                params.append(market)

            query += " ORDER BY market, symbol"
            cursor = conn.execute(query, params)
            return self._rows_to_dicts(cursor.fetchall())

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
        with self._get_conn(self.finance_db) as conn:
            try:
                conn.execute(
                    """INSERT INTO watchlist (symbol, name, market, sector, industry)
                       VALUES (?, ?, ?, ?, ?)
                       ON CONFLICT(symbol) DO UPDATE SET
                           name = COALESCE(excluded.name, name),
                           market = COALESCE(excluded.market, market),
                           sector = COALESCE(excluded.sector, sector),
                           industry = COALESCE(excluded.industry, industry)
                    """,
                    (symbol.upper(), name, market, sector, industry)
                )
                conn.commit()
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
        with self._get_conn(self.finance_db) as conn:
            query = "SELECT * FROM daily_prices WHERE symbol = ?"
            params = [symbol.upper()]

            if start_date:
                query += " AND date >= ?"
                params.append(start_date.isoformat())
            if end_date:
                query += " AND date <= ?"
                params.append(end_date.isoformat())

            query += " ORDER BY date DESC"

            if limit:
                query += " LIMIT ?"
                params.append(limit)

            cursor = conn.execute(query, params)
            return self._rows_to_dicts(cursor.fetchall())

    def get_latest_price(self, symbol: str) -> Optional[Dict]:
        prices = self.get_daily_prices(symbol, limit=1)
        return prices[0] if prices else None

    def get_price_stats(self) -> Dict[str, Any]:
        with self._get_conn(self.finance_db) as conn:
            cursor = conn.execute("SELECT COUNT(*), MIN(date), MAX(date) FROM daily_prices")
            row = cursor.fetchone()
            return {
                "count": row[0] or 0,
                "min_date": row[1],
                "max_date": row[2]
            }

    # ==================== 總經數據 ====================

    def get_macro_data(
        self,
        series_id: str,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        limit: Optional[int] = None
    ) -> List[Dict]:
        if not self.macro_db.exists():
            return []

        with self._get_conn(self.macro_db) as conn:
            query = "SELECT * FROM macro_data WHERE series_id = ?"
            params = [series_id]

            if start_date:
                query += " AND date >= ?"
                params.append(start_date.isoformat())
            if end_date:
                query += " AND date <= ?"
                params.append(end_date.isoformat())

            query += " ORDER BY date DESC"

            if limit:
                query += " LIMIT ?"
                params.append(limit)

            cursor = conn.execute(query, params)
            return self._rows_to_dicts(cursor.fetchall())

    def get_macro_indicators(self, active_only: bool = True) -> List[Dict]:
        if not self.macro_db.exists():
            return []

        with self._get_conn(self.macro_db) as conn:
            query = "SELECT * FROM macro_indicators"
            if active_only:
                query += " WHERE is_active = 1"

            cursor = conn.execute(query)
            return self._rows_to_dicts(cursor.fetchall())

    def get_latest_cycle(self) -> Optional[Dict]:
        if not self.macro_db.exists():
            return None

        with self._get_conn(self.macro_db) as conn:
            cursor = conn.execute(
                "SELECT * FROM market_cycles ORDER BY date DESC LIMIT 1"
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    # ==================== 統計 ====================

    def get_stats(self) -> Dict[str, Any]:
        stats = {
            "news_count": 0,
            "watchlist_count": 0,
            "prices_count": 0,
            "by_market": {},
            "date_range": None
        }

        # 新聞統計
        if self.news_db.exists():
            with self._get_conn(self.news_db) as conn:
                cursor = conn.execute("SELECT COUNT(*) FROM news")
                stats["news_count"] = cursor.fetchone()[0]

        # 股票統計
        if self.finance_db.exists():
            with self._get_conn(self.finance_db) as conn:
                cursor = conn.execute("SELECT COUNT(*) FROM watchlist WHERE is_active = 1")
                stats["watchlist_count"] = cursor.fetchone()[0]

                cursor = conn.execute("SELECT COUNT(*) FROM daily_prices")
                stats["prices_count"] = cursor.fetchone()[0]

                cursor = conn.execute(
                    "SELECT market, COUNT(*) FROM watchlist WHERE is_active = 1 GROUP BY market"
                )
                stats["by_market"] = dict(cursor.fetchall())

                cursor = conn.execute("SELECT MIN(date), MAX(date) FROM daily_prices")
                row = cursor.fetchone()
                if row[0]:
                    stats["date_range"] = {"min": row[0], "max": row[1]}

        return stats

    # ==================== 新聞寫入 ====================

    def insert_news(self, news: Dict) -> bool:
        """插入單筆新聞"""
        with self._get_conn(self.news_db, create_if_missing=True) as conn:
            try:
                conn.execute(
                    """INSERT INTO news (title, content, url, source, category, published_at, source_type)
                       VALUES (?, ?, ?, ?, ?, ?, ?)
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
                conn.commit()
                return conn.total_changes > 0
            except Exception:
                return False

    def insert_news_bulk(self, news_list: List[Dict]) -> int:
        """批量插入新聞"""
        if not news_list:
            return 0

        with self._get_conn(self.news_db, create_if_missing=True) as conn:
            inserted = 0
            for news in news_list:
                try:
                    conn.execute(
                        """INSERT INTO news (title, content, url, source, category, published_at, source_type)
                           VALUES (?, ?, ?, ?, ?, ?, ?)
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
                    if conn.total_changes > inserted:
                        inserted = conn.total_changes
                except Exception:
                    continue
            conn.commit()
            return inserted

    # ==================== 股票清單寫入 ====================

    def update_watchlist_status(self, symbol: str, is_active: bool) -> bool:
        """更新追蹤清單狀態"""
        with self._get_conn(self.finance_db) as conn:
            try:
                conn.execute(
                    "UPDATE watchlist SET is_active = ?, updated_at = CURRENT_TIMESTAMP WHERE symbol = ?",
                    (1 if is_active else 0, symbol.upper())
                )
                conn.commit()
                return conn.total_changes > 0
            except Exception:
                return False

    # ==================== 價格數據寫入 ====================

    def insert_daily_price(self, data: Dict) -> bool:
        """插入單筆每日價格"""
        with self._get_conn(self.finance_db, create_if_missing=True) as conn:
            try:
                conn.execute(
                    """INSERT INTO daily_prices (symbol, date, open, high, low, close, adj_close, volume)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(symbol, date) DO UPDATE SET
                           open = excluded.open,
                           high = excluded.high,
                           low = excluded.low,
                           close = excluded.close,
                           adj_close = excluded.adj_close,
                           volume = excluded.volume
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
                conn.commit()
                return True
            except Exception:
                return False

    def insert_daily_prices_bulk(self, data_list: List[Dict]) -> int:
        """批量插入每日價格 (支援 upsert)"""
        if not data_list:
            return 0

        with self._get_conn(self.finance_db, create_if_missing=True) as conn:
            try:
                conn.executemany(
                    """INSERT INTO daily_prices (symbol, date, open, high, low, close, adj_close, volume)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(symbol, date) DO UPDATE SET
                           open = excluded.open,
                           high = excluded.high,
                           low = excluded.low,
                           close = excluded.close,
                           adj_close = excluded.adj_close,
                           volume = excluded.volume
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
                conn.commit()
                return len(data_list)
            except Exception:
                return 0

    def insert_fundamentals(self, symbol: str, data: Dict) -> bool:
        """插入基本面數據"""
        with self._get_conn(self.finance_db, create_if_missing=True) as conn:
            try:
                conn.execute(
                    """INSERT INTO fundamentals (
                           symbol, date, market_cap, enterprise_value, pe_ratio, forward_pe,
                           peg_ratio, pb_ratio, ps_ratio, dividend_yield, eps, revenue,
                           profit_margin, operating_margin, roe, roa, debt_to_equity,
                           current_ratio, quick_ratio, beta, fifty_two_week_high,
                           fifty_two_week_low, fifty_day_avg, two_hundred_day_avg,
                           avg_volume, shares_outstanding, float_shares, held_by_institutions,
                           short_ratio, raw_data
                       )
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(symbol, date) DO UPDATE SET
                           market_cap = excluded.market_cap,
                           pe_ratio = excluded.pe_ratio,
                           pb_ratio = excluded.pb_ratio,
                           eps = excluded.eps,
                           raw_data = excluded.raw_data
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
                        str(data.get("raw_data")) if data.get("raw_data") else None
                    )
                )
                conn.commit()
                return True
            except Exception:
                return False

    # ==================== 總經數據寫入 ====================

    def insert_macro_indicator(self, indicator: Dict) -> bool:
        """插入總經指標定義"""
        with self._get_conn(self.macro_db, create_if_missing=True) as conn:
            try:
                conn.execute(
                    """INSERT INTO macro_indicators (series_id, name, frequency, category, is_active)
                       VALUES (?, ?, ?, ?, ?)
                       ON CONFLICT(series_id) DO UPDATE SET
                           name = excluded.name,
                           frequency = excluded.frequency,
                           category = excluded.category
                    """,
                    (
                        indicator.get("series_id"),
                        indicator.get("name"),
                        indicator.get("frequency"),
                        indicator.get("category"),
                        1 if indicator.get("is_active", True) else 0
                    )
                )
                conn.commit()
                return True
            except Exception:
                return False

    def insert_macro_data(self, series_id: str, data: Dict) -> bool:
        """插入單筆總經數據"""
        with self._get_conn(self.macro_db, create_if_missing=True) as conn:
            try:
                conn.execute(
                    """INSERT INTO macro_data (series_id, date, value, change_pct)
                       VALUES (?, ?, ?, ?)
                       ON CONFLICT(series_id, date) DO UPDATE SET
                           value = excluded.value,
                           change_pct = excluded.change_pct
                    """,
                    (
                        series_id,
                        data.get("date"),
                        data.get("value"),
                        data.get("change_pct")
                    )
                )
                conn.commit()
                return True
            except Exception:
                return False

    def insert_macro_data_bulk(self, series_id: str, data_list: List[Dict]) -> int:
        """批量插入總經數據"""
        if not data_list:
            return 0

        with self._get_conn(self.macro_db, create_if_missing=True) as conn:
            try:
                conn.executemany(
                    """INSERT INTO macro_data (series_id, date, value, change_pct)
                       VALUES (?, ?, ?, ?)
                       ON CONFLICT(series_id, date) DO UPDATE SET
                           value = excluded.value,
                           change_pct = excluded.change_pct
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
                conn.commit()
                return len(data_list)
            except Exception:
                return 0

    def insert_market_cycle(self, cycle_data: Dict) -> bool:
        """插入市場週期記錄"""
        import json

        with self._get_conn(self.macro_db, create_if_missing=True) as conn:
            try:
                signals = cycle_data.get("signals")
                if signals and not isinstance(signals, str):
                    signals = json.dumps(signals)

                conn.execute(
                    """INSERT INTO market_cycles (date, phase, score, confidence, signals, recommended_strategy)
                       VALUES (?, ?, ?, ?, ?, ?)
                       ON CONFLICT(date) DO UPDATE SET
                           phase = excluded.phase,
                           score = excluded.score,
                           confidence = excluded.confidence,
                           signals = excluded.signals,
                           recommended_strategy = excluded.recommended_strategy
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
                conn.commit()
                return True
            except Exception:
                return False
