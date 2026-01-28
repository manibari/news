#!/usr/bin/env python3
"""
PTT Stock 歷史文章收集腳本

收集過去一年的 PTT Stock 版文章
預估每頁約 20 篇，一年約需爬取 1000-2000 頁
"""

import argparse
import logging
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from src.collectors.ptt_collector import PTTCollector
from src.database import Database

# 設定日誌
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('ptt_historical.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


def collect_ptt_historical(pages: int = 500, target_date: datetime = None):
    """
    收集 PTT Stock 歷史文章

    Args:
        pages: 要爬取的頁數
        target_date: 目標最早日期 (收集到這個日期為止)
    """
    db = Database("news.db")

    if target_date is None:
        target_date = datetime.now() - timedelta(days=365)

    logger.info("=" * 60)
    logger.info("PTT Stock 歷史文章收集")
    logger.info(f"目標: 收集到 {target_date.strftime('%Y-%m-%d')} 為止")
    logger.info(f"預計爬取: {pages} 頁")
    logger.info("=" * 60)

    collector = PTTCollector(pages=1)  # 我們會手動控制頁數
    collector.reset_year_tracking()  # 重置年份追蹤

    total_collected = 0
    total_inserted = 0
    oldest_date = datetime.now()

    current_url = collector.BASE_URL
    page_num = 0

    while page_num < pages:
        try:
            # 爬取單頁（啟用年份追蹤）
            news_items, prev_url = collector._parse_page(current_url, track_year=True)

            # 插入資料庫
            inserted = 0
            reached_target = False
            for news in news_items:
                # 跳過公告文章（通常是置頂，日期可能很舊）
                is_announcement = news.category == "公告" or "[公告]" in news.title

                if news.published_at and not is_announcement:
                    if news.published_at < oldest_date:
                        oldest_date = news.published_at

                    # 如果已經超過目標日期，停止收集
                    if news.published_at < target_date:
                        logger.info(f"已達到目標日期 {target_date.strftime('%Y-%m-%d')}，停止收集")
                        reached_target = True
                        break

                if db.insert_news(news):
                    inserted += 1

            if reached_target:
                break

            total_collected += len(news_items)
            total_inserted += inserted

            page_num += 1

            # 每 10 頁顯示進度
            if page_num % 10 == 0:
                logger.info(f"進度: {page_num}/{pages} 頁 | "
                           f"收集: {total_collected} | 新增: {total_inserted} | "
                           f"最早日期: {oldest_date.strftime('%Y-%m-%d') if oldest_date else 'N/A'}")

            # 檢查是否已達目標日期
            if oldest_date < target_date:
                logger.info(f"已達到目標日期，停止收集")
                break

            if not prev_url:
                logger.info("已到達最後一頁")
                break

            current_url = prev_url

            # 避免請求過快
            time.sleep(0.3)

        except KeyboardInterrupt:
            logger.info("收集中斷")
            break
        except Exception as e:
            logger.error(f"第 {page_num + 1} 頁爬取失敗: {e}")
            time.sleep(2)
            continue

    # 顯示統計
    logger.info("=" * 60)
    logger.info("收集完成")
    logger.info("=" * 60)
    logger.info(f"總爬取頁數: {page_num}")
    logger.info(f"總收集文章: {total_collected}")
    logger.info(f"新增文章數: {total_inserted}")
    logger.info(f"最早文章日期: {oldest_date.strftime('%Y-%m-%d') if oldest_date else 'N/A'}")

    # 顯示資料庫統計
    news_count = db.get_news_count()
    logger.info(f"資料庫總文章數: {news_count}")

    # 顯示各分類統計
    import sqlite3
    conn = sqlite3.connect("news.db")
    cursor = conn.cursor()
    cursor.execute("""
        SELECT category, COUNT(*) as count
        FROM news
        WHERE source = 'PTT Stock'
        GROUP BY category
        ORDER BY count DESC
    """)
    logger.info("\nPTT Stock 文章分類統計:")
    for row in cursor.fetchall():
        logger.info(f"  {row[0]}: {row[1]} 則")
    conn.close()

    return {
        'pages': page_num,
        'collected': total_collected,
        'inserted': total_inserted,
        'oldest_date': oldest_date
    }


def main():
    parser = argparse.ArgumentParser(description="PTT Stock 歷史文章收集")
    parser.add_argument("--pages", type=int, default=500, help="要爬取的頁數 (預設 500)")
    parser.add_argument("--days", type=int, default=365, help="收集過去幾天的文章 (預設 365)")

    args = parser.parse_args()

    target_date = datetime.now() - timedelta(days=args.days)

    try:
        collect_ptt_historical(pages=args.pages, target_date=target_date)
    except Exception as e:
        logger.error(f"收集失敗: {e}")
        raise


if __name__ == "__main__":
    main()
