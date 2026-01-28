#!/usr/bin/env python3
"""
股票歷史數據收集腳本 - 從 2021 年開始收集

使用方式:
    python collect_stock_historical.py              # 收集所有股票 (2021 年至今)
    python collect_stock_historical.py --start 2021-01-01  # 指定開始日期
    python collect_stock_historical.py --symbols AAPL MSFT # 指定股票
"""

import argparse
import logging
import sys
import time
from datetime import datetime, date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from src.finance.database import FinanceDatabase
from src.finance.collector import YFinanceCollector, US_STOCKS, TW_STOCKS, ETFS, INDICES

# 設定日誌
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('stock_historical.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


def collect_historical_stock_data(
    start_date: str = "2021-01-01",
    end_date: str = None,
    symbols: list = None,
    include_fundamentals: bool = True
):
    """
    收集股票歷史數據

    Args:
        start_date: 開始日期 (YYYY-MM-DD)
        end_date: 結束日期
        symbols: 指定股票代碼 (None 則使用 watchlist)
        include_fundamentals: 是否收集基本面數據
    """
    db = FinanceDatabase()
    collector = YFinanceCollector(db)

    if end_date is None:
        end_date = date.today().strftime("%Y-%m-%d")

    logger.info("=" * 60)
    logger.info("股票歷史數據收集")
    logger.info(f"期間: {start_date} ~ {end_date}")
    logger.info("=" * 60)

    # 如果沒有指定股票，先初始化 watchlist
    if symbols is None:
        current_symbols = db.get_symbols()
        if not current_symbols:
            logger.info("\n[Step 1] 初始化股票追蹤清單...")

            # 新增美股
            logger.info("新增美股...")
            collector.add_symbols(US_STOCKS, market="US", fetch_info=True)

            # 新增台股
            logger.info("新增台股...")
            collector.add_symbols(TW_STOCKS, market="TW", fetch_info=True)

            # 新增 ETF
            logger.info("新增 ETF...")
            collector.add_symbols(ETFS, market="ETF", fetch_info=True)

            # 新增指數
            logger.info("新增指數...")
            collector.add_symbols(INDICES, market="INDEX", fetch_info=False)

        symbols = db.get_symbols()

    logger.info(f"\n共 {len(symbols)} 檔股票需要收集")

    # 收集歷史價格數據
    logger.info("\n[Step 2] 收集歷史價格數據...")
    logger.info("-" * 40)

    price_stats = collector.collect_historical_data(
        symbols=symbols,
        start_date=start_date,
        end_date=end_date
    )

    logger.info(f"\n價格數據收集完成:")
    logger.info(f"  收集: {price_stats['collected']} 筆")
    logger.info(f"  新增: {price_stats['inserted']} 筆")
    logger.info(f"  錯誤: {price_stats['errors']} 筆")

    # 收集基本面數據
    if include_fundamentals:
        logger.info("\n[Step 3] 收集基本面數據...")
        logger.info("-" * 40)

        fund_stats = collector.collect_fundamentals(symbols=symbols)

        logger.info(f"\n基本面數據收集完成:")
        logger.info(f"  收集: {fund_stats['collected']} 筆")
        logger.info(f"  新增: {fund_stats['inserted']} 筆")
        logger.info(f"  錯誤: {fund_stats['errors']} 筆")

    # 顯示統計
    logger.info("\n" + "=" * 60)
    logger.info("資料庫統計")
    logger.info("=" * 60)

    stats = db.get_stats()
    logger.info(f"追蹤股票數: {stats['watchlist_count']}")
    logger.info(f"價格數據筆數: {stats['prices_count']}")
    logger.info(f"有價格數據的股票: {stats['symbols_with_prices']}")
    logger.info(f"基本面數據筆數: {stats['fundamentals_count']}")

    if stats['date_range']:
        logger.info(f"日期範圍: {stats['date_range']['min']} ~ {stats['date_range']['max']}")

    if stats['by_market']:
        logger.info("\n按市場分類:")
        for market, count in stats['by_market'].items():
            logger.info(f"  {market}: {count} 檔")

    return stats


def main():
    parser = argparse.ArgumentParser(description="股票歷史數據收集")
    parser.add_argument("--start", default="2021-01-01", help="開始日期 (YYYY-MM-DD)")
    parser.add_argument("--end", default=None, help="結束日期 (YYYY-MM-DD)")
    parser.add_argument("--symbols", nargs="+", help="指定股票代碼")
    parser.add_argument("--no-fundamentals", action="store_true", help="不收集基本面數據")

    args = parser.parse_args()

    try:
        collect_historical_stock_data(
            start_date=args.start,
            end_date=args.end,
            symbols=args.symbols,
            include_fundamentals=not args.no_fundamentals
        )
    except KeyboardInterrupt:
        logger.info("\n收集中斷")
    except Exception as e:
        logger.error(f"收集失敗: {e}")
        raise


if __name__ == "__main__":
    main()
