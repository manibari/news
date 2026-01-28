#!/usr/bin/env python3
"""
金融數據收集系統 - 主程式入口
使用 yfinance 收集股票價格和基本面數據
"""

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import List

sys.path.insert(0, str(Path(__file__).parent))

from src.finance import FinanceDatabase, YFinanceCollector
from src.finance.collector import US_STOCKS, TW_STOCKS, ETFS, INDICES

# 設定日誌
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# 預設資料庫路徑
DEFAULT_DB_PATH = "finance.db"


def init_watchlist(db: FinanceDatabase, collector: YFinanceCollector, fast_mode: bool = False):
    """初始化股票追蹤清單"""
    logger.info("初始化股票追蹤清單...")
    if fast_mode:
        logger.info("(快速模式：跳過 yfinance API 查詢)")

    fetch_info = not fast_mode

    logger.info("\n[1/4] 新增美股...")
    collector.add_symbols(US_STOCKS, market="US", fetch_info=fetch_info)

    logger.info("\n[2/4] 新增台股...")
    collector.add_symbols(TW_STOCKS, market="TW", fetch_info=fetch_info)

    logger.info("\n[3/4] 新增 ETF...")
    collector.add_symbols(ETFS, market="ETF", fetch_info=fetch_info)

    logger.info("\n[4/4] 新增指數...")
    collector.add_symbols(INDICES, market="INDEX", fetch_info=fetch_info)

    logger.info("\n初始化完成！")
    show_stats(db)


def collect_daily(db: FinanceDatabase, collector: YFinanceCollector, market: str = None):
    """收集每日數據"""
    symbols = db.get_symbols(market=market) if market else None

    logger.info("=" * 50)
    logger.info(f"開始每日數據收集 - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    if market:
        logger.info(f"市場: {market}")
    logger.info("=" * 50)

    results = collector.collect_all(symbols=symbols, price_period="5d")

    return results


def collect_historical(db: FinanceDatabase, collector: YFinanceCollector,
                       period: str = "1y", market: str = None):
    """收集歷史數據"""
    symbols = db.get_symbols(market=market) if market else None

    logger.info("=" * 50)
    logger.info(f"開始歷史數據收集 - 期間: {period}")
    if market:
        logger.info(f"市場: {market}")
    logger.info("=" * 50)

    results = collector.collect_historical_data(symbols=symbols, period=period)

    logger.info(f"\n收集完成: {results['collected']} 筆, 新增 {results['inserted']} 筆")
    return results


def show_stats(db: FinanceDatabase):
    """顯示資料庫統計"""
    stats = db.get_stats()

    logger.info("\n" + "=" * 50)
    logger.info("資料庫統計")
    logger.info("=" * 50)
    logger.info(f"追蹤股票數: {stats['watchlist_count']}")
    logger.info(f"價格數據筆數: {stats['prices_count']}")
    logger.info(f"有價格數據的股票: {stats['symbols_with_prices']}")
    logger.info(f"基本面數據筆數: {stats['fundamentals_count']}")

    if stats["by_market"]:
        logger.info("\n依市場分佈:")
        for market, count in stats["by_market"].items():
            logger.info(f"  {market}: {count}")

    if stats["date_range"]:
        logger.info(f"\n數據日期範圍: {stats['date_range']['min']} ~ {stats['date_range']['max']}")


def add_custom_symbols(db: FinanceDatabase, collector: YFinanceCollector,
                       symbols: List[str], market: str):
    """新增自訂股票"""
    logger.info(f"新增 {len(symbols)} 個股票到 {market}...")
    added = collector.add_symbols(symbols, market=market)
    logger.info(f"完成！新增 {added} 個")


def main():
    parser = argparse.ArgumentParser(
        description="金融數據收集系統 (yfinance)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
範例:
  python finance_collector.py --init           初始化股票清單（首次使用）
  python finance_collector.py --init --fast    快速初始化（避免 rate limit）
  python finance_collector.py --daily          收集每日數據
  python finance_collector.py --daily --market US  只收集美股
  python finance_collector.py --historical 1y  收集一年歷史數據
  python finance_collector.py --stats          顯示統計
  python finance_collector.py --add AAPL,MSFT --market US  新增股票
        """,
    )

    parser.add_argument("--init", action="store_true", help="初始化股票追蹤清單")
    parser.add_argument("--fast", action="store_true", help="快速模式（跳過 yfinance API 查詢名稱）")
    parser.add_argument("--daily", action="store_true", help="收集每日數據")
    parser.add_argument("--historical", type=str, metavar="PERIOD",
                        help="收集歷史數據 (1mo, 3mo, 6mo, 1y, 2y, 5y, max)")
    parser.add_argument("--fundamentals", action="store_true", help="只收集基本面數據")
    parser.add_argument("--stats", action="store_true", help="顯示資料庫統計")
    parser.add_argument("--market", type=str, choices=["US", "TW", "ETF", "INDEX"],
                        help="指定市場")
    parser.add_argument("--add", type=str, metavar="SYMBOLS",
                        help="新增股票 (逗號分隔)")
    parser.add_argument("--db", type=str, default=DEFAULT_DB_PATH,
                        help=f"資料庫路徑 (預設: {DEFAULT_DB_PATH})")

    args = parser.parse_args()

    # 初始化資料庫和收集器
    db = FinanceDatabase(db_path=args.db)
    collector = YFinanceCollector(db)

    logger.info(f"資料庫已連接: {args.db}")

    if args.init:
        init_watchlist(db, collector, fast_mode=args.fast)
    elif args.daily:
        collect_daily(db, collector, market=args.market)
    elif args.historical:
        collect_historical(db, collector, period=args.historical, market=args.market)
    elif args.fundamentals:
        symbols = db.get_symbols(market=args.market) if args.market else None
        collector.collect_fundamentals(symbols=symbols)
    elif args.stats:
        show_stats(db)
    elif args.add:
        if not args.market:
            logger.error("請指定市場 --market (US/TW/ETF/INDEX)")
            return
        symbols = [s.strip().upper() for s in args.add.split(",")]
        collector.add_symbols(symbols, market=args.market)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
