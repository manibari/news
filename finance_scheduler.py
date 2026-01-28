#!/usr/bin/env python3
"""
金融數據自動排程收集
- 美股收盤後 (台灣時間 06:00) 收集所有數據
- 台股收盤後 (台灣時間 14:30) 收集台股數據
"""

import argparse
import logging
import time
from datetime import datetime
from pathlib import Path
import sys

import schedule

sys.path.insert(0, str(Path(__file__).parent))

from src.finance import FinanceDatabase, YFinanceCollector

# 設定日誌
log_dir = Path("logs")
log_dir.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(log_dir / "finance_scheduler.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# 預設資料庫路徑
DEFAULT_DB_PATH = "finance.db"


def collect_all_data():
    """收集所有市場的數據"""
    logger.info("=" * 60)
    logger.info("開始收集所有市場數據")
    logger.info("=" * 60)

    try:
        db = FinanceDatabase(db_path=DEFAULT_DB_PATH)
        collector = YFinanceCollector(db)

        # 收集每日價格 (最近5天以確保完整)
        logger.info("\n[Phase 1] 收集每日價格數據...")
        price_results = collector.collect_daily_prices(period="5d")
        logger.info(f"價格數據: 收集 {price_results['collected']} 筆, 新增 {price_results['inserted']} 筆")

        # 收集基本面數據
        logger.info("\n[Phase 2] 收集基本面數據...")
        fund_results = collector.collect_fundamentals()
        logger.info(f"基本面: 收集 {fund_results['collected']} 筆, 新增 {fund_results['inserted']} 筆")

        logger.info("\n收集完成！")

    except Exception as e:
        logger.error(f"收集失敗: {e}")


def collect_tw_data():
    """只收集台股數據"""
    logger.info("=" * 60)
    logger.info("開始收集台股數據")
    logger.info("=" * 60)

    try:
        db = FinanceDatabase(db_path=DEFAULT_DB_PATH)
        collector = YFinanceCollector(db)

        # 取得台股代碼
        tw_symbols = db.get_symbols(market="TW")
        # 加上台灣 ETF
        etf_symbols = db.get_symbols(market="ETF")
        tw_etfs = [s for s in etf_symbols if s.endswith(".TW")]

        all_tw = tw_symbols + tw_etfs

        if not all_tw:
            logger.warning("沒有台股需要收集")
            return

        logger.info(f"收集 {len(all_tw)} 檔台股...")

        # 收集價格
        price_results = collector.collect_daily_prices(symbols=all_tw, period="5d")
        logger.info(f"價格數據: 收集 {price_results['collected']} 筆, 新增 {price_results['inserted']} 筆")

        logger.info("台股收集完成！")

    except Exception as e:
        logger.error(f"台股收集失敗: {e}")


def run_scheduler():
    """執行排程"""
    logger.info("金融數據排程啟動")
    logger.info("-" * 40)
    logger.info("排程時間:")
    logger.info("  - 06:00 收集所有市場數據 (美股收盤後)")
    logger.info("  - 14:30 收集台股數據 (台股收盤後)")
    logger.info("-" * 40)
    logger.info("按 Ctrl+C 停止排程")

    # 設定排程
    schedule.every().day.at("06:00").do(collect_all_data)
    schedule.every().day.at("14:30").do(collect_tw_data)

    logger.info("已設定 06:00 執行全市場收集")
    logger.info("已設定 14:30 執行台股收集")

    while True:
        schedule.run_pending()
        time.sleep(60)


def main():
    parser = argparse.ArgumentParser(
        description="金融數據自動排程收集",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
範例:
  python finance_scheduler.py --schedule      啟動排程模式
  python finance_scheduler.py --once          立即執行一次 (所有市場)
  python finance_scheduler.py --once --tw     立即執行一次 (只有台股)
        """
    )

    parser.add_argument("--schedule", action="store_true", help="啟動排程模式")
    parser.add_argument("--once", action="store_true", help="立即執行一次")
    parser.add_argument("--tw", action="store_true", help="只收集台股")
    parser.add_argument("--db", type=str, default=DEFAULT_DB_PATH, help="資料庫路徑")

    args = parser.parse_args()

    # 更新資料庫路徑
    db_path = args.db

    if args.schedule:
        run_scheduler()
    elif args.once:
        if args.tw:
            collect_tw_data()
        else:
            collect_all_data()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
