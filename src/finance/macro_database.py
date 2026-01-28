"""
總經數據資料庫模組
"""

import sqlite3
from contextlib import contextmanager
from datetime import datetime, date
from pathlib import Path
from typing import Generator, List, Optional, Dict
import json

import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from config.macro_indicators import MACRO_DATABASE_PATH


class MacroDatabase:
    """總經數據 SQLite 資料庫管理類別"""

    def __init__(self, db_path: str = None):
        """
        初始化資料庫連接

        Args:
            db_path: 資料庫檔案路徑
        """
        self.db_path = Path(db_path or MACRO_DATABASE_PATH)
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

            # 總經指標定義表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS macro_indicators (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    series_id TEXT UNIQUE NOT NULL,
                    name TEXT NOT NULL,
                    name_en TEXT,
                    frequency TEXT,
                    category TEXT,
                    description TEXT,
                    unit TEXT,
                    is_active INTEGER DEFAULT 1,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # 總經數據表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS macro_data (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    series_id TEXT NOT NULL,
                    date DATE NOT NULL,
                    value REAL NOT NULL,
                    change_pct REAL,
                    change_value REAL,
                    collected_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(series_id, date),
                    FOREIGN KEY (series_id) REFERENCES macro_indicators(series_id)
                )
            """)

            # 市場週期記錄表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS market_cycles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date DATE UNIQUE NOT NULL,
                    phase TEXT NOT NULL,
                    score REAL NOT NULL,
                    confidence REAL,
                    signals TEXT,
                    recommended_strategy TEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # 建立索引
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_macro_data_series ON macro_data(series_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_macro_data_date ON macro_data(date)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_macro_indicators_category ON macro_indicators(category)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_market_cycles_date ON market_cycles(date)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_market_cycles_phase ON market_cycles(phase)")

    # ========== Macro Indicators 操作 ==========

    def add_indicator(self, series_id: str, name: str, name_en: str = None,
                      frequency: str = None, category: str = None,
                      description: str = None, unit: str = None) -> bool:
        """新增總經指標定義"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute("""
                    INSERT INTO macro_indicators
                    (series_id, name, name_en, frequency, category, description, unit)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (series_id, name, name_en, frequency, category, description, unit))
                return True
            except sqlite3.IntegrityError:
                # 已存在，更新資料
                cursor.execute("""
                    UPDATE macro_indicators
                    SET name = COALESCE(?, name),
                        name_en = COALESCE(?, name_en),
                        frequency = COALESCE(?, frequency),
                        category = COALESCE(?, category),
                        description = COALESCE(?, description),
                        unit = COALESCE(?, unit),
                        updated_at = CURRENT_TIMESTAMP
                    WHERE series_id = ?
                """, (name, name_en, frequency, category, description, unit, series_id))
                return False

    def get_indicators(self, category: str = None, active_only: bool = True) -> List[dict]:
        """取得指標定義"""
        with self._get_connection() as conn:
            cursor = conn.cursor()

            query = "SELECT * FROM macro_indicators WHERE 1=1"
            params = []

            if active_only:
                query += " AND is_active = 1"
            if category:
                query += " AND category = ?"
                params.append(category)

            query += " ORDER BY category, series_id"
            cursor.execute(query, params)

            return [dict(row) for row in cursor.fetchall()]

    def get_indicator(self, series_id: str) -> Optional[dict]:
        """取得單一指標定義"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM macro_indicators WHERE series_id = ?
            """, (series_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_series_ids(self, category: str = None) -> List[str]:
        """取得所有指標代碼"""
        indicators = self.get_indicators(category=category)
        return [item["series_id"] for item in indicators]

    # ========== Macro Data 操作 ==========

    def insert_macro_data(self, series_id: str, date: date, value: float,
                          change_pct: float = None, change_value: float = None) -> bool:
        """插入總經數據"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute("""
                    INSERT INTO macro_data
                    (series_id, date, value, change_pct, change_value)
                    VALUES (?, ?, ?, ?, ?)
                """, (series_id, date, value, change_pct, change_value))
                return True
            except sqlite3.IntegrityError:
                # 已存在，更新數值
                cursor.execute("""
                    UPDATE macro_data
                    SET value = ?,
                        change_pct = COALESCE(?, change_pct),
                        change_value = COALESCE(?, change_value),
                        collected_at = CURRENT_TIMESTAMP
                    WHERE series_id = ? AND date = ?
                """, (value, change_pct, change_value, series_id, date))
                return False

    def insert_macro_data_bulk(self, data: List[dict]) -> int:
        """批量插入總經數據"""
        inserted = 0
        for row in data:
            if self.insert_macro_data(
                series_id=row["series_id"],
                date=row["date"],
                value=row["value"],
                change_pct=row.get("change_pct"),
                change_value=row.get("change_value")
            ):
                inserted += 1
        return inserted

    def get_macro_data(self, series_id: str, start_date: date = None,
                       end_date: date = None, limit: int = None) -> List[dict]:
        """取得總經數據"""
        with self._get_connection() as conn:
            cursor = conn.cursor()

            query = "SELECT * FROM macro_data WHERE series_id = ?"
            params = [series_id]

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

    def get_latest_macro_data(self, series_id: str) -> Optional[dict]:
        """取得最新總經數據"""
        data = self.get_macro_data(series_id, limit=1)
        return data[0] if data else None

    def get_latest_date(self, series_id: str) -> Optional[date]:
        """取得最新資料日期"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT MAX(date) FROM macro_data WHERE series_id = ?
            """, (series_id,))
            result = cursor.fetchone()[0]
            return datetime.strptime(result, "%Y-%m-%d").date() if result else None

    def get_all_latest_data(self) -> Dict[str, dict]:
        """取得所有指標的最新數據"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT md.*, mi.name, mi.name_en, mi.category, mi.unit
                FROM macro_data md
                INNER JOIN macro_indicators mi ON md.series_id = mi.series_id
                WHERE md.date = (
                    SELECT MAX(date) FROM macro_data
                    WHERE series_id = md.series_id
                )
                AND mi.is_active = 1
            """)

            result = {}
            for row in cursor.fetchall():
                row_dict = dict(row)
                result[row_dict["series_id"]] = row_dict
            return result

    def get_data_by_category(self, category: str, limit: int = 30) -> Dict[str, List[dict]]:
        """按類別取得數據"""
        indicators = self.get_indicators(category=category)
        result = {}
        for indicator in indicators:
            result[indicator["series_id"]] = self.get_macro_data(
                indicator["series_id"], limit=limit
            )
        return result

    # ========== Market Cycles 操作 ==========

    def insert_market_cycle(self, date: date, phase: str, score: float,
                            confidence: float = None, signals: dict = None,
                            recommended_strategy: str = None) -> bool:
        """插入市場週期記錄"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute("""
                    INSERT INTO market_cycles
                    (date, phase, score, confidence, signals, recommended_strategy)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    date, phase, score, confidence,
                    json.dumps(signals) if signals else None,
                    recommended_strategy
                ))
                return True
            except sqlite3.IntegrityError:
                # 已存在，更新
                cursor.execute("""
                    UPDATE market_cycles
                    SET phase = ?,
                        score = ?,
                        confidence = ?,
                        signals = ?,
                        recommended_strategy = ?
                    WHERE date = ?
                """, (
                    phase, score, confidence,
                    json.dumps(signals) if signals else None,
                    recommended_strategy, date
                ))
                return False

    def get_market_cycles(self, start_date: date = None, end_date: date = None,
                          limit: int = None) -> List[dict]:
        """取得市場週期記錄"""
        with self._get_connection() as conn:
            cursor = conn.cursor()

            query = "SELECT * FROM market_cycles WHERE 1=1"
            params = []

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

            results = []
            for row in cursor.fetchall():
                row_dict = dict(row)
                if row_dict.get("signals"):
                    row_dict["signals"] = json.loads(row_dict["signals"])
                results.append(row_dict)
            return results

    def get_latest_market_cycle(self) -> Optional[dict]:
        """取得最新市場週期"""
        cycles = self.get_market_cycles(limit=1)
        return cycles[0] if cycles else None

    def get_cycle_history(self, days: int = 90) -> List[dict]:
        """取得週期歷史"""
        from datetime import timedelta
        start_date = date.today() - timedelta(days=days)
        return self.get_market_cycles(start_date=start_date)

    # ========== 統計查詢 ==========

    def get_stats(self) -> dict:
        """取得資料庫統計"""
        with self._get_connection() as conn:
            cursor = conn.cursor()

            cursor.execute("SELECT COUNT(*) FROM macro_indicators WHERE is_active = 1")
            indicators_count = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM macro_data")
            data_count = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(DISTINCT series_id) FROM macro_data")
            series_with_data = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM market_cycles")
            cycles_count = cursor.fetchone()[0]

            cursor.execute("""
                SELECT category, COUNT(*)
                FROM macro_indicators
                WHERE is_active = 1
                GROUP BY category
            """)
            by_category = dict(cursor.fetchall())

            cursor.execute("SELECT MIN(date), MAX(date) FROM macro_data")
            date_range = cursor.fetchone()

            cursor.execute("""
                SELECT phase, COUNT(*)
                FROM market_cycles
                GROUP BY phase
            """)
            cycles_by_phase = dict(cursor.fetchall())

            return {
                "indicators_count": indicators_count,
                "data_count": data_count,
                "series_with_data": series_with_data,
                "cycles_count": cycles_count,
                "by_category": by_category,
                "cycles_by_phase": cycles_by_phase,
                "date_range": {
                    "min": date_range[0],
                    "max": date_range[1]
                } if date_range[0] else None
            }

    def clear_old_data(self, days_to_keep: int = 365) -> int:
        """清除舊數據"""
        from datetime import timedelta
        cutoff_date = date.today() - timedelta(days=days_to_keep)

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM macro_data WHERE date < ?", (cutoff_date,))
            deleted = cursor.rowcount
            return deleted
