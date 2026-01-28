#!/usr/bin/env python3
"""
歷史新聞收集腳本 - 收集指定日期範圍的新聞
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))

from config.settings import NEWS_API_KEY
from src.database import Database
from src.database.models import News
from src.utils.helpers import clean_text, parse_date

# 擴展關鍵字以獲取更多新聞
KEYWORDS = [
    "economy",
    "market",
    "inflation",
    "Federal Reserve",
    "earnings",
    "stock",
    "gold",
    "oil",
    "tariff",
    "trade",
    "GDP",
    "employment",
    "dollar",
    "treasury",
    "AI artificial intelligence",
    "tech layoff",
    "bitcoin crypto",
]


def collect_news_for_period(from_date: str, to_date: str, db: Database):
    """
    收集指定期間的新聞

    Args:
        from_date: 開始日期 (YYYY-MM-DD)
        to_date: 結束日期 (YYYY-MM-DD)
        db: Database 實例
    """
    base_url = "https://newsapi.org/v2/everything"

    total_collected = 0
    total_inserted = 0

    for keyword in KEYWORDS:
        print(f"搜尋關鍵字: {keyword}")

        params = {
            "q": keyword,
            "from": from_date,
            "to": to_date,
            "sortBy": "publishedAt",
            "language": "en",
            "pageSize": 100,  # 最大值
            "apiKey": NEWS_API_KEY,
        }

        try:
            response = requests.get(base_url, params=params, timeout=30)
            response.raise_for_status()
            data = response.json()

            if data.get("status") != "ok":
                print(f"  錯誤: {data.get('message', 'Unknown error')}")
                continue

            articles = data.get("articles", [])
            print(f"  找到 {len(articles)} 則新聞")

            inserted = 0
            for article in articles:
                title = clean_text(article.get("title", ""))
                url = article.get("url", "")

                if not title or not url:
                    continue

                source = "NewsAPI"
                if article.get("source") and article["source"].get("name"):
                    source = article["source"]["name"]

                # 解析發布時間
                published_at = parse_date(article.get("publishedAt"))

                news = News(
                    title=title,
                    content=clean_text(article.get("description")),
                    url=url,
                    source=source,
                    category="macro",
                    published_at=published_at,
                    collected_at=published_at or datetime.now(),  # 使用發布時間作為收集時間
                    source_type="api",
                )

                if db.insert_news(news) is not None:
                    inserted += 1

            total_collected += len(articles)
            total_inserted += inserted
            print(f"  新增 {inserted} 則")

        except Exception as e:
            print(f"  錯誤: {e}")

    return total_collected, total_inserted


def main():
    db = Database("news.db")

    print("=" * 60)
    print("歷史新聞收集")
    print("=" * 60)

    # NewsAPI 免費版限制：只能查詢過去約 30 天
    # 嘗試收集 2025-12-28 到 2026-01-28 的新聞

    # 分段收集以避免 API 限制
    periods = [
        ("2025-12-28", "2026-01-05"),
        ("2026-01-06", "2026-01-15"),
        ("2026-01-16", "2026-01-28"),
    ]

    grand_total_collected = 0
    grand_total_inserted = 0

    for from_date, to_date in periods:
        print(f"\n收集期間: {from_date} ~ {to_date}")
        print("-" * 40)

        collected, inserted = collect_news_for_period(from_date, to_date, db)
        grand_total_collected += collected
        grand_total_inserted += inserted

        print(f"期間小計: 收集 {collected}, 新增 {inserted}")

    print("\n" + "=" * 60)
    print("收集完成總結")
    print("=" * 60)
    print(f"總收集數: {grand_total_collected}")
    print(f"總新增數: {grand_total_inserted}")
    print(f"資料庫總筆數: {db.get_news_count()}")

    # 顯示各日期的新聞數量
    print("\n各日期新聞統計:")
    conn = db._get_connection()
    with conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT date(collected_at) as news_date, COUNT(*) as count
            FROM news
            GROUP BY date(collected_at)
            ORDER BY news_date DESC
            LIMIT 35
        """)
        for row in cursor.fetchall():
            print(f"  {row[0]}: {row[1]} 則")


if __name__ == "__main__":
    main()
