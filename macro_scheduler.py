#!/usr/bin/env python3
"""
總經數據排程收集器

排程設定:
- 每日 08:00: 更新每日指標 (殖利率曲線、VIX)
- 每週日 06:00: 完整更新所有指標

使用方式:
    python macro_scheduler.py          # 啟動排程
    python macro_scheduler.py --once   # 執行一次並退出
    python macro_scheduler.py --daily  # 只執行每日更新
    python macro_scheduler.py --full   # 執行完整更新
"""

import argparse
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import schedule

# 設定路徑
sys.path.insert(0, str(Path(__file__).parent))

from config.macro_indicators import MACRO_SCHEDULE
from src.finance.macro_collector import FREDCollector
from src.finance.macro_database import MacroDatabase
from src.finance.cycle_analyzer import MarketCycleAnalyzer


# 設定日誌
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('macro_scheduler.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


class MacroScheduler:
    """總經數據排程器"""

    def __init__(self):
        self.db = MacroDatabase()
        self.collector = FREDCollector(db=self.db)
        self.analyzer = MarketCycleAnalyzer(db=self.db)

    def daily_update(self):
        """每日更新 - 更新每日指標"""
        logger.info("=" * 50)
        logger.info("開始每日更新...")

        try:
            # 收集每日更新的指標
            results = self.collector.collect_daily_updates()
            logger.info(f"每日指標更新完成: {len(results)} 個指標")

            for series_id, count in results.items():
                logger.info(f"  - {series_id}: {count} 筆資料")

            # 執行週期分析
            self._analyze_cycle()

            logger.info("每日更新完成")

        except Exception as e:
            logger.error(f"每日更新失敗: {e}")

    def weekly_update(self):
        """每週更新 - 更新所有指標"""
        logger.info("=" * 50)
        logger.info("開始每週完整更新...")

        try:
            # 收集所有指標
            results = self.collector.collect_monthly_updates()
            logger.info(f"完整更新完成: {len(results)} 個指標")

            for series_id, count in results.items():
                logger.info(f"  - {series_id}: {count} 筆資料")

            # 執行週期分析
            self._analyze_cycle()

            # 顯示資料庫統計
            stats = self.db.get_stats()
            logger.info(f"資料庫統計:")
            logger.info(f"  - 指標數: {stats['indicators_count']}")
            logger.info(f"  - 數據筆數: {stats['data_count']}")
            if stats['date_range']:
                logger.info(f"  - 日期範圍: {stats['date_range']['min']} ~ {stats['date_range']['max']}")

            logger.info("每週完整更新完成")

        except Exception as e:
            logger.error(f"每週更新失敗: {e}")

    def full_update(self):
        """完整更新 - 收集所有歷史數據"""
        logger.info("=" * 50)
        logger.info("開始完整歷史數據收集...")

        try:
            # 設定指標
            count = self.collector.setup_indicators()
            logger.info(f"已設定 {count} 個指標")

            # 收集所有歷史數據 (最近 2 年)
            results = self.collector.collect_all()
            logger.info(f"歷史數據收集完成: {len(results)} 個指標")

            for series_id, count in results.items():
                logger.info(f"  - {series_id}: {count} 筆資料")

            # 執行週期分析
            self._analyze_cycle()

            logger.info("完整歷史數據收集完成")

        except Exception as e:
            logger.error(f"完整更新失敗: {e}")

    def _analyze_cycle(self):
        """執行週期分析"""
        try:
            logger.info("執行市場週期分析...")
            cycle = self.analyzer.get_current_cycle()
            logger.info(f"當前週期: {cycle['phase_emoji']} {cycle['phase_name']} "
                       f"(分數: {cycle['score']:.2f}, 信心: {cycle['confidence']:.0%})")
        except Exception as e:
            logger.error(f"週期分析失敗: {e}")

    def setup_schedule(self):
        """設定排程"""
        daily_time = MACRO_SCHEDULE.get("daily_update_time", "08:00")
        weekly_day = MACRO_SCHEDULE.get("weekly_full_update_day", "sunday")
        weekly_time = MACRO_SCHEDULE.get("weekly_full_update_time", "06:00")

        # 每日更新
        schedule.every().day.at(daily_time).do(self.daily_update)
        logger.info(f"已設定每日更新: {daily_time}")

        # 每週完整更新
        if weekly_day == "sunday":
            schedule.every().sunday.at(weekly_time).do(self.weekly_update)
        elif weekly_day == "saturday":
            schedule.every().saturday.at(weekly_time).do(self.weekly_update)
        elif weekly_day == "monday":
            schedule.every().monday.at(weekly_time).do(self.weekly_update)
        logger.info(f"已設定每週更新: {weekly_day} {weekly_time}")

    def run(self):
        """啟動排程"""
        logger.info("總經數據排程器啟動")
        logger.info(f"PID: {os.getpid()}")

        self.setup_schedule()

        # 啟動時執行一次每日更新
        self.daily_update()

        logger.info("排程器運行中... (Ctrl+C 停止)")

        while True:
            schedule.run_pending()
            time.sleep(60)  # 每分鐘檢查一次


def main():
    parser = argparse.ArgumentParser(description="總經數據排程收集器")
    parser.add_argument("--once", action="store_true", help="執行一次每日更新並退出")
    parser.add_argument("--daily", action="store_true", help="執行每日更新")
    parser.add_argument("--weekly", action="store_true", help="執行每週更新")
    parser.add_argument("--full", action="store_true", help="執行完整歷史數據收集")

    args = parser.parse_args()

    scheduler = MacroScheduler()

    if args.once or args.daily:
        scheduler.daily_update()
    elif args.weekly:
        scheduler.weekly_update()
    elif args.full:
        scheduler.full_update()
    else:
        # 預設: 啟動持續運行的排程
        try:
            scheduler.run()
        except KeyboardInterrupt:
            logger.info("排程器已停止")


if __name__ == "__main__":
    main()
