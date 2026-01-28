"""
設定檔 - API keys、RSS URLs 等配置
"""

# NewsAPI 設定
# 請至 https://newsapi.org/ 申請免費 API Key
NEWS_API_KEY = "69ab7787f74e4e30aace0e4442dce10a"

# RSS Feed 來源設定
RSS_FEEDS = [
    {
        "name": "Reuters Business",
        "url": "https://feeds.reuters.com/reuters/businessNews",
        "category": "macro"
    },
    {
        "name": "CNBC Top News",
        "url": "https://www.cnbc.com/id/100003114/device/rss/rss.html",
        "category": "macro"
    },
    {
        "name": "MarketWatch",
        "url": "http://feeds.marketwatch.com/marketwatch/topstories/",
        "category": "macro"
    },
]

# NewsAPI 搜尋關鍵字
NEWS_API_KEYWORDS = [
    "economy",
    "market",
    "inflation",
    "Federal Reserve",
    "earnings",
]

# 網頁爬蟲目標
SCRAPER_TARGETS = [
    {
        "name": "Yahoo Finance",
        "url": "https://finance.yahoo.com/topic/economic-news/",
        "category": "macro"
    },
]

# 資料庫設定
DATABASE_PATH = "news.db"

# 排程設定
SCHEDULE_TIMES = ["08:00", "20:00"]  # 每日執行時間 (每 12 小時)
SCHEDULE_INTERVAL_HOURS = 12  # 執行間隔（小時）

# 請求設定
REQUEST_TIMEOUT = 30  # 秒
REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
}
