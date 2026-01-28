"""
金融數據資料庫模組
"""

import sqlite3
from contextlib import contextmanager
from datetime import datetime, date
from pathlib import Path
from typing import Generator, List, Optional, Dict
import json


class FinanceDatabase:
    """金融數據 SQLite 資料庫管理類別"""

    def __init__(self, db_path: str = "finance.db"):
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

            # 股票/ETF/指數 追蹤清單
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS watchlist (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT UNIQUE NOT NULL,
                    name TEXT,
                    market TEXT,
                    sector TEXT,
                    industry TEXT,
                    is_active INTEGER DEFAULT 1,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # 每日價格 (OHLCV)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS daily_prices (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    date DATE NOT NULL,
                    open REAL,
                    high REAL,
                    low REAL,
                    close REAL,
                    adj_close REAL,
                    volume INTEGER,
                    collected_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(symbol, date)
                )
            """)

            # 基本面數據
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS fundamentals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    date DATE NOT NULL,
                    market_cap REAL,
                    enterprise_value REAL,
                    pe_ratio REAL,
                    forward_pe REAL,
                    peg_ratio REAL,
                    pb_ratio REAL,
                    ps_ratio REAL,
                    dividend_yield REAL,
                    eps REAL,
                    revenue REAL,
                    profit_margin REAL,
                    operating_margin REAL,
                    roe REAL,
                    roa REAL,
                    debt_to_equity REAL,
                    current_ratio REAL,
                    quick_ratio REAL,
                    beta REAL,
                    fifty_two_week_high REAL,
                    fifty_two_week_low REAL,
                    fifty_day_avg REAL,
                    two_hundred_day_avg REAL,
                    avg_volume REAL,
                    shares_outstanding REAL,
                    float_shares REAL,
                    held_by_institutions REAL,
                    short_ratio REAL,
                    raw_data TEXT,
                    collected_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(symbol, date)
                )
            """)

            # 建立索引
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_daily_symbol ON daily_prices(symbol)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_daily_date ON daily_prices(date)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_fund_symbol ON fundamentals(symbol)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_watchlist_market ON watchlist(market)")

    # ========== Watchlist 操作 ==========

    def add_to_watchlist(self, symbol: str, name: str = None, market: str = None,
                         sector: str = None, industry: str = None) -> bool:
        """新增股票到追蹤清單"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute("""
                    INSERT INTO watchlist (symbol, name, market, sector, industry)
                    VALUES (?, ?, ?, ?, ?)
                """, (symbol.upper(), name, market, sector, industry))
                return True
            except sqlite3.IntegrityError:
                # 已存在，更新資料
                cursor.execute("""
                    UPDATE watchlist
                    SET name = COALESCE(?, name),
                        market = COALESCE(?, market),
                        sector = COALESCE(?, sector),
                        industry = COALESCE(?, industry),
                        updated_at = CURRENT_TIMESTAMP
                    WHERE symbol = ?
                """, (name, market, sector, industry, symbol.upper()))
                return False

    def get_watchlist(self, market: str = None, active_only: bool = True) -> List[dict]:
        """取得追蹤清單"""
        with self._get_connection() as conn:
            cursor = conn.cursor()

            query = "SELECT * FROM watchlist WHERE 1=1"
            params = []

            if active_only:
                query += " AND is_active = 1"
            if market:
                query += " AND market = ?"
                params.append(market)

            query += " ORDER BY market, symbol"
            cursor.execute(query, params)

            return [dict(row) for row in cursor.fetchall()]

    def get_symbols(self, market: str = None) -> List[str]:
        """取得所有追蹤的股票代碼"""
        watchlist = self.get_watchlist(market=market)
        return [item["symbol"] for item in watchlist]

    # ========== Daily Prices 操作 ==========

    def insert_daily_price(self, symbol: str, date: date, open_price: float,
                           high: float, low: float, close: float,
                           adj_close: float, volume: int) -> bool:
        """插入每日價格"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute("""
                    INSERT INTO daily_prices
                    (symbol, date, open, high, low, close, adj_close, volume)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (symbol.upper(), date, open_price, high, low, close, adj_close, volume))
                return True
            except sqlite3.IntegrityError:
                return False

    def insert_daily_prices_bulk(self, data: List[dict]) -> int:
        """批量插入每日價格"""
        inserted = 0
        for row in data:
            if self.insert_daily_price(
                symbol=row["symbol"],
                date=row["date"],
                open_price=row["open"],
                high=row["high"],
                low=row["low"],
                close=row["close"],
                adj_close=row.get("adj_close", row["close"]),
                volume=row["volume"]
            ):
                inserted += 1
        return inserted

    def get_daily_prices(self, symbol: str, start_date: date = None,
                         end_date: date = None, limit: int = None) -> List[dict]:
        """取得每日價格"""
        with self._get_connection() as conn:
            cursor = conn.cursor()

            query = "SELECT * FROM daily_prices WHERE symbol = ?"
            params = [symbol.upper()]

            if start_date:
                query += " AND date >= ?"
                params.append(start_date)
            if end_date:
                query += " AND date <= ?"
                params.append(end_date)

            query += " ORDER BY date DESC"

            if limit:
                query += " LIMIT ?"
                params.append(limit)

            cursor.execute(query, params)
            return [dict(row) for row in cursor.fetchall()]

    def get_latest_price(self, symbol: str) -> Optional[dict]:
        """取得最新價格"""
        prices = self.get_daily_prices(symbol, limit=1)
        return prices[0] if prices else None

    def get_latest_date(self, symbol: str) -> Optional[date]:
        """取得最新資料日期"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT MAX(date) FROM daily_prices WHERE symbol = ?
            """, (symbol.upper(),))
            result = cursor.fetchone()[0]
            return datetime.strptime(result, "%Y-%m-%d").date() if result else None

    # ========== Fundamentals 操作 ==========

    def insert_fundamentals(self, symbol: str, date: date, data: dict) -> bool:
        """插入基本面數據"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute("""
                    INSERT INTO fundamentals
                    (symbol, date, market_cap, enterprise_value, pe_ratio, forward_pe,
                     peg_ratio, pb_ratio, ps_ratio, dividend_yield, eps, revenue,
                     profit_margin, operating_margin, roe, roa, debt_to_equity,
                     current_ratio, quick_ratio, beta, fifty_two_week_high,
                     fifty_two_week_low, fifty_day_avg, two_hundred_day_avg,
                     avg_volume, shares_outstanding, float_shares, held_by_institutions,
                     short_ratio, raw_data)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    symbol.upper(), date,
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
                    json.dumps(data.get("raw_data", {}))
                ))
                return True
            except sqlite3.IntegrityError:
                return False

    def get_fundamentals(self, symbol: str, limit: int = 1) -> List[dict]:
        """取得基本面數據"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM fundamentals
                WHERE symbol = ?
                ORDER BY date DESC
                LIMIT ?
            """, (symbol.upper(), limit))
            return [dict(row) for row in cursor.fetchall()]

    # ========== 統計查詢 ==========

    def get_stats(self) -> dict:
        """取得資料庫統計"""
        with self._get_connection() as conn:
            cursor = conn.cursor()

            cursor.execute("SELECT COUNT(*) FROM watchlist WHERE is_active = 1")
            watchlist_count = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM daily_prices")
            prices_count = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(DISTINCT symbol) FROM daily_prices")
            symbols_with_prices = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM fundamentals")
            fundamentals_count = cursor.fetchone()[0]

            cursor.execute("SELECT market, COUNT(*) FROM watchlist WHERE is_active = 1 GROUP BY market")
            by_market = dict(cursor.fetchall())

            cursor.execute("SELECT MIN(date), MAX(date) FROM daily_prices")
            date_range = cursor.fetchone()

            return {
                "watchlist_count": watchlist_count,
                "prices_count": prices_count,
                "symbols_with_prices": symbols_with_prices,
                "fundamentals_count": fundamentals_count,
                "by_market": by_market,
                "date_range": {
                    "min": date_range[0],
                    "max": date_range[1]
                } if date_range[0] else None
            }
