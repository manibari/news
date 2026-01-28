#!/usr/bin/env python3
"""
總經與產業新聞收集系統 - 主程式入口
"""

import argparse
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

import schedule

# 將專案根目錄加入 Python path
sys.path.insert(0, str(Path(__file__).parent))

from config.settings import (
    DATABASE_PATH,
    NEWS_API_KEY,
    NEWS_API_KEYWORDS,
    REQUEST_HEADERS,
    RSS_FEEDS,
    SCHEDULE_TIMES,
    SCHEDULE_INTERVAL_HOURS,
    SCRAPER_TARGETS,
)
from src.collectors import NewsAPICollector, RSSCollector, WebScraper, PTTCollector
from src.database import Database

# 設定日誌
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def collect_all_news(db: Database) -> dict:
    """
    執行所有新聞收集器

    Args:
        db: Database 實例

    Returns:
        收集結果統計
    """
    stats = {
        "rss": {"collected": 0, "inserted": 0},
        "api": {"collected": 0, "inserted": 0},
        "scraper": {"collected": 0, "inserted": 0},
        "ptt": {"collected": 0, "inserted": 0},
    }

    logger.info("=" * 50)
    logger.info(f"開始新聞收集 - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("=" * 50)

    # 1. RSS Feed 收集
    logger.info("\n[Phase 1] RSS Feed 收集")
    logger.info("-" * 30)
    try:
        rss_collector = RSSCollector(feeds=RSS_FEEDS)
        rss_news = rss_collector.collect()
        stats["rss"]["collected"] = len(rss_news)
        stats["rss"]["inserted"] = db.insert_many(rss_news)
        logger.info(
            f"RSS 收集完成: 收集 {stats['rss']['collected']} 則, "
            f"新增 {stats['rss']['inserted']} 則"
        )
    except Exception as e:
        logger.error(f"RSS 收集發生錯誤: {e}")

    # 2. NewsAPI 收集
    logger.info("\n[Phase 2] NewsAPI 收集")
    logger.info("-" * 30)
    try:
        api_collector = NewsAPICollector(
            api_key=NEWS_API_KEY,
            keywords=NEWS_API_KEYWORDS,
        )
        api_news = api_collector.collect()
        stats["api"]["collected"] = len(api_news)
        stats["api"]["inserted"] = db.insert_many(api_news)
        logger.info(
            f"API 收集完成: 收集 {stats['api']['collected']} 則, "
            f"新增 {stats['api']['inserted']} 則"
        )
    except Exception as e:
        logger.error(f"API 收集發生錯誤: {e}")

    # 3. 網頁爬蟲收集
    logger.info("\n[Phase 3] 網頁爬蟲收集")
    logger.info("-" * 30)
    try:
        scraper = WebScraper(
            targets=SCRAPER_TARGETS,
            headers=REQUEST_HEADERS,
        )
        scraper_news = scraper.collect()
        stats["scraper"]["collected"] = len(scraper_news)
        stats["scraper"]["inserted"] = db.insert_many(scraper_news)
        logger.info(
            f"爬蟲收集完成: 收集 {stats['scraper']['collected']} 則, "
            f"新增 {stats['scraper']['inserted']} 則"
        )
    except Exception as e:
        logger.error(f"爬蟲收集發生錯誤: {e}")

    # 4. PTT Stock 收集
    logger.info("\n[Phase 4] PTT Stock 收集")
    logger.info("-" * 30)
    try:
        ptt_collector = PTTCollector(pages=3)  # 爬取 3 頁
        ptt_news = ptt_collector.collect()
        stats["ptt"]["collected"] = len(ptt_news)
        stats["ptt"]["inserted"] = db.insert_many(ptt_news)
        logger.info(
            f"PTT 收集完成: 收集 {stats['ptt']['collected']} 則, "
            f"新增 {stats['ptt']['inserted']} 則"
        )
    except Exception as e:
        logger.error(f"PTT 收集發生錯誤: {e}")

    # 總結
    total_collected = sum(s["collected"] for s in stats.values())
    total_inserted = sum(s["inserted"] for s in stats.values())

    logger.info("\n" + "=" * 50)
    logger.info("收集完成總結")
    logger.info("=" * 50)
    logger.info(f"總收集數: {total_collected} 則")
    logger.info(f"新增數量: {total_inserted} 則")
    logger.info(f"重複略過: {total_collected - total_inserted} 則")
    logger.info(f"資料庫總筆數: {db.get_news_count()} 則")

    return stats


def show_stats(db: Database) -> None:
    """顯示資料庫統計"""
    logger.info("\n" + "=" * 50)
    logger.info("資料庫統計")
    logger.info("=" * 50)
    logger.info(f"總新聞數: {db.get_news_count()} 則")

    by_source = db.get_news_by_source_type()
    logger.info("\n依來源類型:")
    for source_type, count in by_source.items():
        logger.info(f"  - {source_type}: {count} 則")

    recent = db.get_recent_news(5)
    if recent:
        logger.info("\n最近 5 則新聞:")
        for news in recent:
            logger.info(f"  [{news.source}] {news.title[:50]}...")


def run_scheduler(db: Database) -> None:
    """執行排程模式"""
    logger.info(f"排程模式啟動，每 {SCHEDULE_INTERVAL_HOURS} 小時執行一次新聞收集")
    logger.info(f"排程時間: {', '.join(SCHEDULE_TIMES)}")
    logger.info("按 Ctrl+C 停止排程")

    # 設定排程 - 每天在指定時間執行
    for schedule_time in SCHEDULE_TIMES:
        schedule.every().day.at(schedule_time).do(collect_all_news, db=db)
        logger.info(f"  - 已設定 {schedule_time} 執行")

    # 執行排程迴圈
    while True:
        schedule.run_pending()
        time.sleep(60)  # 每分鐘檢查一次


def main():
    """主程式入口"""
    parser = argparse.ArgumentParser(
        description="總經與產業新聞收集系統",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
範例:
  python main.py --once      單次執行新聞收集
  python main.py --schedule  啟動排程模式
  python main.py --stats     顯示資料庫統計
        """,
    )

    parser.add_argument(
        "--once",
        action="store_true",
        help="單次執行新聞收集",
    )
    parser.add_argument(
        "--schedule",
        action="store_true",
        help="啟動排程模式",
    )
    parser.add_argument(
        "--stats",
        action="store_true",
        help="顯示資料庫統計",
    )
    parser.add_argument(
        "--db",
        type=str,
        default=DATABASE_PATH,
        help=f"資料庫路徑 (預設: {DATABASE_PATH})",
    )

    args = parser.parse_args()

    # 初始化資料庫
    db = Database(db_path=args.db)
    logger.info(f"資料庫已連接: {args.db}")

    if args.stats:
        show_stats(db)
    elif args.schedule:
        try:
            run_scheduler(db)
        except KeyboardInterrupt:
            logger.info("\n排程已停止")
    elif args.once:
        collect_all_news(db)
    else:
        # 預設顯示說明
        parser.print_help()


if __name__ == "__main__":
    main()
