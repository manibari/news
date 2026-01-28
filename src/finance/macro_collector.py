"""
FRED API 總經數據收集器
"""

import logging
from datetime import datetime, date, timedelta
from typing import List, Dict, Optional
from pathlib import Path

try:
    from fredapi import Fred
except ImportError:
    Fred = None

import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from config.macro_indicators import (
    FRED_API_KEY,
    MACRO_INDICATORS,
)
from src.finance.macro_database import MacroDatabase


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class FREDCollector:
    """FRED API 數據收集器"""

    def __init__(self, api_key: str = None, db: MacroDatabase = None):
        """
        初始化收集器

        Args:
            api_key: FRED API Key
            db: MacroDatabase 實例
        """
        if Fred is None:
            raise ImportError("請安裝 fredapi: pip install fredapi")

        self.api_key = api_key or FRED_API_KEY
        self.fred = Fred(api_key=self.api_key)
        self.db = db or MacroDatabase()

    def setup_indicators(self) -> int:
        """初始化指標定義到資料庫"""
        count = 0
        for indicator in MACRO_INDICATORS:
            self.db.add_indicator(
                series_id=indicator["series_id"],
                name=indicator["name"],
                name_en=indicator.get("name_en"),
                frequency=indicator.get("frequency"),
                category=indicator.get("category"),
                description=indicator.get("description"),
                unit=indicator.get("unit")
            )
            count += 1
            logger.info(f"已設定指標: {indicator['series_id']} - {indicator['name']}")
        return count

    def collect_series(self, series_id: str, start_date: date = None,
                       end_date: date = None) -> List[dict]:
        """
        收集單一指標數據

        Args:
            series_id: FRED 系列 ID
            start_date: 開始日期
            end_date: 結束日期

        Returns:
            收集的數據列表
        """
        try:
            # 預設取最近 5 年數據
            if start_date is None:
                start_date = date.today() - timedelta(days=365*5)
            if end_date is None:
                end_date = date.today()

            logger.info(f"正在收集 {series_id} ({start_date} ~ {end_date})...")

            # 從 FRED 取得數據
            series = self.fred.get_series(
                series_id,
                observation_start=start_date,
                observation_end=end_date
            )

            if series is None or series.empty:
                logger.warning(f"無法取得 {series_id} 數據")
                return []

            # 轉換為列表格式
            data = []
            prev_value = None
            for obs_date, value in series.items():
                if value is not None and not (isinstance(value, float) and value != value):
                    obs_date = obs_date.date() if hasattr(obs_date, 'date') else obs_date

                    # 計算變化
                    change_value = None
                    change_pct = None
                    if prev_value is not None and prev_value != 0:
                        change_value = value - prev_value
                        change_pct = (change_value / abs(prev_value)) * 100

                    data.append({
                        "series_id": series_id,
                        "date": obs_date,
                        "value": float(value),
                        "change_value": change_value,
                        "change_pct": change_pct
                    })
                    prev_value = value

            # 存入資料庫
            inserted = self.db.insert_macro_data_bulk(data)
            logger.info(f"已收集 {series_id}: {len(data)} 筆資料, {inserted} 筆新增")

            return data

        except Exception as e:
            logger.error(f"收集 {series_id} 失敗: {e}")
            return []

    def collect_all(self, start_date: date = None, end_date: date = None) -> Dict[str, int]:
        """
        收集所有指標數據

        Args:
            start_date: 開始日期
            end_date: 結束日期

        Returns:
            各指標收集數量
        """
        results = {}

        # 確保指標已設定
        self.setup_indicators()

        # 取得所有活躍指標
        indicators = self.db.get_indicators(active_only=True)

        for indicator in indicators:
            series_id = indicator["series_id"]
            data = self.collect_series(series_id, start_date, end_date)
            results[series_id] = len(data)

        logger.info(f"完成收集 {len(results)} 個指標")
        return results

    def collect_daily_updates(self) -> Dict[str, int]:
        """收集每日更新的指標"""
        results = {}

        indicators = self.db.get_indicators(active_only=True)
        daily_indicators = [i for i in indicators if i["frequency"] == "daily"]

        # 取最近 7 天數據以確保不遺漏
        start_date = date.today() - timedelta(days=7)

        for indicator in daily_indicators:
            series_id = indicator["series_id"]
            data = self.collect_series(series_id, start_date=start_date)
            results[series_id] = len(data)

        logger.info(f"每日更新完成: {len(results)} 個指標")
        return results

    def collect_weekly_updates(self) -> Dict[str, int]:
        """收集每週更新的指標"""
        results = {}

        indicators = self.db.get_indicators(active_only=True)
        weekly_indicators = [i for i in indicators if i["frequency"] in ["daily", "weekly"]]

        # 取最近 14 天數據
        start_date = date.today() - timedelta(days=14)

        for indicator in weekly_indicators:
            series_id = indicator["series_id"]
            data = self.collect_series(series_id, start_date=start_date)
            results[series_id] = len(data)

        logger.info(f"每週更新完成: {len(results)} 個指標")
        return results

    def collect_monthly_updates(self) -> Dict[str, int]:
        """收集每月更新的指標"""
        results = {}

        indicators = self.db.get_indicators(active_only=True)

        # 取最近 60 天數據以確保捕捉到月度數據
        start_date = date.today() - timedelta(days=60)

        for indicator in indicators:
            series_id = indicator["series_id"]
            data = self.collect_series(series_id, start_date=start_date)
            results[series_id] = len(data)

        logger.info(f"每月更新完成: {len(results)} 個指標")
        return results

    def get_series_info(self, series_id: str) -> Optional[dict]:
        """取得指標詳細資訊"""
        try:
            info = self.fred.get_series_info(series_id)
            return {
                "id": info.get("id"),
                "title": info.get("title"),
                "frequency": info.get("frequency"),
                "units": info.get("units"),
                "seasonal_adjustment": info.get("seasonal_adjustment"),
                "last_updated": info.get("last_updated"),
                "notes": info.get("notes")
            }
        except Exception as e:
            logger.error(f"取得 {series_id} 資訊失敗: {e}")
            return None

    def search_series(self, search_text: str, limit: int = 10) -> List[dict]:
        """搜尋指標"""
        try:
            results = self.fred.search(search_text, limit=limit)
            series_list = []
            for _, row in results.iterrows():
                series_list.append({
                    "id": row.get("id"),
                    "title": row.get("title"),
                    "frequency": row.get("frequency"),
                    "units": row.get("units"),
                    "popularity": row.get("popularity")
                })
            return series_list
        except Exception as e:
            logger.error(f"搜尋失敗: {e}")
            return []


def main():
    """主程式 - 用於測試"""
    print("FRED 數據收集器")
    print("=" * 50)

    collector = FREDCollector()

    # 設定指標
    print("\n1. 設定指標定義...")
    count = collector.setup_indicators()
    print(f"   已設定 {count} 個指標")

    # 收集所有數據
    print("\n2. 收集歷史數據...")
    results = collector.collect_all()
    for series_id, count in results.items():
        print(f"   {series_id}: {count} 筆")

    # 顯示統計
    print("\n3. 資料庫統計:")
    stats = collector.db.get_stats()
    print(f"   指標數: {stats['indicators_count']}")
    print(f"   數據筆數: {stats['data_count']}")
    if stats['date_range']:
        print(f"   日期範圍: {stats['date_range']['min']} ~ {stats['date_range']['max']}")


if __name__ == "__main__":
    main()
