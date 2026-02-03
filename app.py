"""
新聞瀏覽 & 股票數據 Streamlit 應用程式

支援多種資料庫後端：
- SQLite (DB_TYPE=sqlite)
- PostgreSQL (DB_TYPE=postgresql)
- Supabase (DB_TYPE=supabase)
"""

import sqlite3
import os
from datetime import datetime, date, timedelta
from pathlib import Path
from collections import defaultdict

import pandas as pd
import numpy as np
import streamlit as st
import plotly.graph_objects as go
from plotly.subplots import make_subplots

# 加入分析模組
import sys
sys.path.insert(0, str(Path(__file__).parent))
from src.finance.analyzer import TechnicalAnalyzer
from src.finance.portfolio_strategy import PortfolioStrategy
from src.finance.macro_database import MacroDatabase
from src.finance.cycle_analyzer import MarketCycleAnalyzer
from src.finance.cycle_strategy import CycleBasedStrategySelector
from src.finance.cycle_backtest import CycleBacktester
from src.finance.sentiment_backtest import SentimentBacktester, DailyHotStocksAnalyzer

# ==================== 資料層初始化 ====================
# 使用統一的資料抽象層，透過 DB_TYPE 環境變數選擇後端
from src.data import get_client, get_client_info

# 延遲初始化資料客戶端
DATA_CLIENT = None
DB_TYPE = os.getenv("DB_TYPE", "sqlite").lower()

# 向後兼容：USE_SUPABASE 標誌
USE_SUPABASE = DB_TYPE == "supabase"
SUPABASE_CLIENT = None  # 不再直接使用，改用 DATA_CLIENT


def _get_data_client():
    """取得資料客戶端（延遲初始化）"""
    global DATA_CLIENT
    if DATA_CLIENT is None:
        DATA_CLIENT = get_client()
    return DATA_CLIENT

# ==================== Supabase 快取層 ====================
@st.cache_data(ttl=300)  # 快取 5 分鐘
def _cached_supabase_news(date_str: str):
    """快取新聞查詢 - 使用 collected_at 作為主要日期篩選"""
    # 取得當天收集的新聞 (非 PTT)
    result1 = SUPABASE_CLIENT.table("news").select(
        "id, title, content, url, source, category, source_type, published_at, collected_at"
    ).neq("source_type", "ptt").gte("collected_at", f"{date_str}T00:00:00").lte(
        "collected_at", f"{date_str}T23:59:59"
    ).limit(500).execute()

    # 取得當天發布的 PTT 新聞
    result2 = SUPABASE_CLIENT.table("news").select(
        "id, title, content, url, source, category, source_type, published_at, collected_at"
    ).eq("source_type", "ptt").gte("published_at", f"{date_str}T00:00:00").lte(
        "published_at", f"{date_str}T23:59:59"
    ).limit(200).execute()

    # 合併並排序
    all_news = (result1.data or []) + (result2.data or [])
    all_news.sort(key=lambda x: x.get("collected_at") or x.get("published_at") or "", reverse=True)
    return all_news

@st.cache_data(ttl=300)
def _cached_supabase_weekly_news(start_str: str, end_str: str):
    """快取週新聞查詢 - 使用 collected_at 作為主要日期篩選"""
    # 非 PTT: 用 collected_at
    result1 = SUPABASE_CLIENT.table("news").select(
        "id, title, content, url, source, category, source_type, published_at, collected_at"
    ).neq("source_type", "ptt").gte("collected_at", start_str).lte(
        "collected_at", f"{end_str}T23:59:59"
    ).limit(1500).execute()

    # PTT: 用 published_at
    result2 = SUPABASE_CLIENT.table("news").select(
        "id, title, content, url, source, category, source_type, published_at, collected_at"
    ).eq("source_type", "ptt").gte("published_at", start_str).lte(
        "published_at", f"{end_str}T23:59:59"
    ).limit(500).execute()

    all_news = (result1.data or []) + (result2.data or [])
    all_news.sort(key=lambda x: x.get("collected_at") or x.get("published_at") or "", reverse=True)
    return all_news

@st.cache_data(ttl=600)  # 快取 10 分鐘
def _cached_supabase_watchlist():
    """快取股票清單"""
    result = SUPABASE_CLIENT.table("watchlist").select(
        "symbol, name, market, sector, industry"
    ).eq("is_active", True).order("market").order("symbol").execute()
    return result.data if result.data else []

@st.cache_data(ttl=300)
def _cached_supabase_prices(symbol: str, start_str: str, end_str: str):
    """快取股價查詢"""
    result = SUPABASE_CLIENT.table("daily_prices").select(
        "date, open, high, low, close, volume"
    ).eq("symbol", symbol).gte("date", start_str).lte("date", end_str).order("date").execute()
    return result.data if result.data else []

@st.cache_data(ttl=3600)  # 快取 1 小時
def _cached_supabase_available_dates():
    """快取可用日期 - 使用 collected_at"""
    end_date = date.today()
    start_date = end_date - timedelta(days=90)
    result = SUPABASE_CLIENT.table("news").select("collected_at").gte(
        "collected_at", start_date.isoformat()
    ).order("collected_at", desc=True).limit(5000).execute()
    dates_set = set()
    for r in result.data or []:
        if r.get("collected_at"):
            dates_set.add(r["collected_at"][:10])
    return sorted(dates_set, reverse=True)

# ==================== 新聞篩選器 ====================
# 社論/評論關鍵字 (標題中出現這些詞會被過濾)
EDITORIAL_KEYWORDS = [
    # 英文
    "opinion", "editorial", "commentary", "column", "op-ed", "analysis:",
    "perspective", "viewpoint", "my view", "i think", "in my opinion",
    # 中文
    "社論", "評論", "專欄", "觀點", "看法", "我認為", "個人觀點", "淺見",
]

# 不可靠來源 (這些來源的文章會被過濾)
UNRELIABLE_SOURCES = [
    # 可以根據需要添加
]


def extract_ptt_push_count(content: str) -> int:
    """從 PTT 內容字串提取推文數

    格式: "[推數] 作者: xxx" 或 "[爆] 作者: xxx" 或 "[X1] 作者: xxx"
    """
    if not content:
        return 0

    try:
        # 取得 [] 內的內容
        if "]" in content:
            push_str = content.split("]")[0].replace("[", "").strip()

            # 爆 = 100+ 推
            if "爆" in push_str:
                return 100

            # X 開頭 = 負推 (噓)
            if push_str.startswith("X"):
                return -1

            # 純數字
            if push_str.isdigit():
                return int(push_str)

            # 空白或其他
            return 0
    except:
        return 0

    return 0


def is_editorial_content(news: dict) -> bool:
    """判斷是否為社論/評論類內容"""
    title = (news.get("title") or "").lower()
    source = (news.get("source") or "").lower()

    # 檢查來源
    if source in [s.lower() for s in UNRELIABLE_SOURCES]:
        return True

    # 檢查標題關鍵字
    for keyword in EDITORIAL_KEYWORDS:
        if keyword.lower() in title:
            return True

    return False


def filter_news(news_list: list, ptt_min_push: int = 30, exclude_editorial: bool = True) -> list:
    """過濾新聞列表

    Args:
        news_list: 新聞列表
        ptt_min_push: PTT 最低推文數 (預設 30)
        exclude_editorial: 是否排除社論/評論 (預設 True)

    Returns:
        過濾後的新聞列表
    """
    filtered = []

    for news in news_list:
        source_type = news.get("source_type") or ""

        # PTT 文章：檢查推文數
        if source_type == "ptt":
            push_count = extract_ptt_push_count(news.get("content") or "")
            if push_count < ptt_min_push:
                continue

        # 非 PTT 文章：檢查是否為社論
        elif exclude_editorial:
            if is_editorial_content(news):
                continue

        filtered.append(news)

    return filtered


# 頁面設定
st.set_page_config(
    page_title="股票數據與新聞分析",
    page_icon="📈",
    layout="wide",
)

# 資料庫路徑 (優先使用完整資料庫，若不存在則使用示範資料庫)
_base_path = Path(__file__).parent
DB_PATH = _base_path / "news.db" if (_base_path / "news.db").exists() else _base_path / "demo_news.db"
FINANCE_DB_PATH = _base_path / "finance.db" if (_base_path / "finance.db").exists() else _base_path / "demo_finance.db"
DEMO_MODE = not USE_SUPABASE and "demo" in str(DB_PATH)

# 新聞分類關鍵字
MACRO_KEYWORDS = {
    "Fed/利率": ["fed", "federal reserve", "interest rate", "rate cut", "rate hike", "monetary policy", "fomc"],
    "通膨": ["inflation", "cpi", "pce", "consumer price", "deflation"],
    "GDP/經濟成長": ["gdp", "economic growth", "recession", "economy grow", "economic expansion"],
    "就業": ["jobs", "unemployment", "employment", "labor market", "jobless", "payroll", "hiring"],
    "美元/匯率": ["dollar", "currency", "forex", "exchange rate", "yen", "euro", "yuan"],
    "黃金/避險": ["gold", "silver", "precious metal", "safe haven"],
    "債券/殖利率": ["bond", "treasury", "yield", "10-year", "debt"],
    "貿易/關稅": ["tariff", "trade war", "trade deal", "import", "export", "trade policy"],
    "政府政策": ["government shutdown", "fiscal", "stimulus", "budget", "congress", "white house"],
}

INDUSTRY_KEYWORDS = {
    "半導體": [
        "semiconductor", "chip", "chipmaker", "foundry", "wafer",
        "nvidia", "nvda", "amd", "intel", "qualcomm", "broadcom", "texas instruments",
        "tsmc", "台積電", "asml", "lam research", "applied materials",
        "micron", "sk hynix", "samsung semiconductor"
    ],
    "軟體/雲端": [
        "software", "cloud", "saas", "paas", "iaas",
        "microsoft", "msft", "azure", "salesforce", "oracle", "sap",
        "adobe", "servicenow", "workday", "snowflake", "datadog",
        "crowdstrike", "palo alto", "fortinet", "zscaler"
    ],
    "網路/社群": [
        "social media", "search", "advertising", "digital ad",
        "meta", "facebook", "instagram", "google", "alphabet", "youtube",
        "tiktok", "bytedance", "snap", "pinterest", "twitter", "x corp",
        "linkedin", "reddit"
    ],
    "硬體/消費電子": [
        "hardware", "computer", "pc", "laptop", "smartphone", "phone",
        "apple", "aapl", "iphone", "ipad", "mac", "蘋果",
        "dell", "hp", "lenovo", "samsung electronics", "xiaomi",
        "server", "data center", "伺服器", "資料中心"
    ],
    "AI人工智慧": [
        "ai", "artificial intelligence", "machine learning", "deep learning",
        "generative ai", "genai", "chatgpt", "openai", "anthropic", "claude",
        "copilot", "gemini", "llm", "large language model",
        "palantir", "c3.ai", "ai chip", "ai server"
    ],
    "金融/銀行": ["bank", "financial", "jpmorgan", "goldman", "morgan stanley", "wells fargo", "credit", "lending"],
    "醫療保健": ["healthcare", "pharma", "biotech", "drug", "fda", "hospital", "unitedhealth", "humana", "medicare"],
    "通訊服務": [
        "telecom", "verizon", "at&t", "t-mobile", "comcast", "charter",
        "netflix", "disney", "warner", "paramount", "spotify",
        "streaming", "media", "entertainment", "broadcast", "5g network",
        "中華電", "台灣大", "遠傳"
    ],
    "工業": [
        "industrial", "manufacturing", "machinery", "caterpillar", "deere",
        "honeywell", "3m", "ge", "general electric", "siemens",
        "defense", "lockheed", "raytheon", "northrop", "aerospace",
        "construction", "infrastructure", "railroad", "union pacific"
    ],
    "公用事業": [
        "utility", "utilities", "electric utility", "power grid",
        "nextera", "duke energy", "southern company", "dominion",
        "water utility", "natural gas utility", "regulated utility",
        "台電", "中油", "electricity", "power plant"
    ],
    "基礎材料": [
        "materials", "mining", "copper", "aluminum", "lithium",
        "dow", "basf", "dupont", "linde",
        "gold mining", "iron ore", "commodity", "raw material",
        "fertilizer", "paper"
    ],
    "鋼鐵/石化/水泥": [
        "steel", "鋼鐵", "中鋼", "中鴻", "豐興", "nucor", "us steel", "鋼價",
        "petrochemical", "石化", "台塑", "南亞", "台化", "台塑化", "塑化", "乙烯", "pvc",
        "cement", "水泥", "台泥", "亞泥", "營建材料"
    ],
    "汽車": ["auto", "car", "ev", "electric vehicle", "tesla", "gm", "ford", "toyota", "byd"],
    "能源": ["oil", "gas", "energy", "crude", "opec", "renewable", "solar", "wind", "petroleum"],
    "零售/消費": ["retail", "consumer", "walmart", "amazon", "target", "costco", "spending", "e-commerce"],
    "航空/運輸": ["airline", "aviation", "boeing", "airbus", "ups", "fedex", "shipping", "logistics"],
    "房地產": ["real estate", "housing", "mortgage", "home price", "property", "reit"],
    "加密貨幣": ["crypto", "bitcoin", "ethereum", "blockchain", "defi", "web3", "btc", "eth"],
}

# 科技產業鏈關鍵字
TECH_SUPPLY_CHAIN_KEYWORDS = {
    "AI應用/平台": [
        "genai", "生成式ai", "大型語言模型", "llm", "機器學習", "machine learning",
        "copilot", "chatgpt", "anthropic", "openai", "claude", "gemini", "grok",
        "ai agent", "ai助理", "自動駕駛", "autonomous", "computer vision",
        "palantir", "pltr", "c3.ai", "soundhound", "bigbear"
    ],
    "SaaS/雲服務": [
        "saas", "paas", "iaas", "cloud service", "雲服務", "訂閱",
        "aws", "amazon web services", "azure", "gcp", "google cloud",
        "salesforce", "crm", "servicenow", "now", "workday", "wday",
        "snowflake", "snow", "datadog", "ddog", "mongodb", "mdb",
        "crowdstrike", "crwd", "zscaler", "okta", "twilio", "hubspot"
    ],
    "科技巨頭": [
        "microsoft", "msft", "微軟", "meta", "facebook", "臉書",
        "alphabet", "google", "googl", "goog", "谷歌",
        "amazon", "amzn", "亞馬遜", "apple", "aapl", "蘋果",
        "nvidia", "nvda", "輝達", "tesla", "tsla", "特斯拉",
        "magnificent seven", "mag7", "七巨頭", "科技七雄"
    ],
    "AI基礎設施": [
        "ai chip", "ai晶片", "gpu", "data center", "資料中心",
        "nvidia", "h100", "h200", "b100", "b200", "blackwell", "hopper",
        "ai server", "ai伺服器", "液冷", "liquid cooling",
        "高速運算", "hpc", "超級電腦", "supercomputer"
    ],
    "晶圓代工": [
        "foundry", "晶圓代工", "台積電", "tsmc", "2330", "三星晶圓", "samsung foundry",
        "先進製程", "3nm", "2nm", "5nm", "7nm", "製程", "wafer", "晶圓廠",
        "聯電", "umc", "2303", "格芯", "globalfoundries"
    ],
    "IC設計": [
        "ic design", "fabless", "聯發科", "mediatek", "2454", "高通", "qualcomm",
        "博通", "broadcom", "amd", "intel", "marvell", "瑞昱", "2379",
        "聯詠", "3034", "novatek", "驅動ic", "電源管理ic", "pmic"
    ],
    "記憶體": [
        "dram", "nand", "memory", "記憶體", "hbm", "高頻寬記憶體",
        "三星", "samsung memory", "sk hynix", "海力士", "美光", "micron",
        "南亞科", "2408", "華邦電", "2344", "旺宏", "2337"
    ],
    "封測": [
        "packaging", "封裝", "測試", "osat", "日月光", "aseh", "3711",
        "矽品", "spil", "力成", "6239", "京元電", "2449",
        "先進封裝", "cowos", "chiplet", "2.5d", "3d封裝"
    ],
    "PCB/載板": [
        "pcb", "電路板", "載板", "substrate", "abf載板",
        "欣興", "3037", "南電", "8046", "景碩", "3189",
        "臻鼎", "健鼎", "華通", "2313"
    ],
    "面板/顯示": [
        "panel", "display", "面板", "lcd", "oled", "mini led", "micro led",
        "友達", "2409", "群創", "3481", "彩晶", "6116",
        "京東方", "boe", "lg display", "三星顯示"
    ],
    "被動元件": [
        "mlcc", "passive", "被動元件", "電容", "電阻", "電感",
        "國巨", "2327", "華新科", "2492", "村田", "murata",
        "tdk", "yageo"
    ],
    "網通設備": [
        "networking", "網通", "交換器", "switch", "router", "路由器",
        "思科", "cisco", "arista", "智邦", "2345", "啟碁", "6285",
        "中磊", "5388", "wifi", "5g", "光通訊", "optical"
    ],
    "伺服器/ODM": [
        "server", "伺服器", "odm", "oem", "白牌",
        "廣達", "2382", "緯創", "3231", "英業達", "2356",
        "鴻海", "2317", "foxconn", "和碩", "4938",
        "supermicro", "戴爾", "dell", "hpe"
    ],
    "消費電子": [
        "smartphone", "手機", "筆電", "notebook", "pc", "平板", "tablet",
        "蘋果", "apple", "iphone", "mac", "ipad",
        "三星手機", "小米", "xiaomi", "oppo", "vivo"
    ],
    "半導體設備": [
        "semiconductor equipment", "半導體設備", "光刻機", "lithography",
        "asml", "艾司摩爾", "應材", "applied materials", "amat",
        "lam research", "科林研發", "tokyo electron", "東京威力"
    ],
}

# 情緒分析關鍵字
POSITIVE_KEYWORDS = [
    "surge", "soar", "jump", "gain", "rise", "rally", "record high", "beat", "exceed",
    "growth", "profit", "boom", "optimis", "bullish", "upgrade", "strong", "recover",
    "success", "breakthrough", "expand", "increase", "positive", "better than expected",
    "outperform", "upbeat", "confident", "improve", "advance", "climb",
    # Fed/利率相關 - 降息對股市是利多
    "rate cut", "cut rate", "cuts rate", "降息", "寬鬆", "dovish", "easing"
]

NEGATIVE_KEYWORDS = [
    "plunge", "crash", "fall", "drop", "decline", "slump", "tumble", "sink", "lose",
    "loss", "layoff", "fire", "recession", "crisis", "fear", "worry", "concern",
    "pessimis", "bearish", "downgrade", "weak", "miss", "disappoint", "warn", "threat",
    "risk", "uncertain", "volatile", "trouble", "struggle", "fail", "worse than expected",
    "shutdown", "default", "bankruptcy",
    # Fed/利率相關 - 升息對股市是利空
    "rate hike", "hike rate", "hikes rate", "升息", "緊縮", "hawkish", "tightening"
]


def analyze_sentiment(news_items: list) -> tuple:
    """
    分析新聞情緒，回傳 (燈號, 分數)
    🟢 正面 | 🟡 中性 | 🔴 負面
    """
    if not news_items:
        return "🟡", 0

    positive_count = 0
    negative_count = 0

    for news in news_items:
        text = (news["title"] + " " + (news["content"] or "")).lower()

        for kw in POSITIVE_KEYWORDS:
            if kw in text:
                positive_count += 1

        for kw in NEGATIVE_KEYWORDS:
            if kw in text:
                negative_count += 1

    total = positive_count + negative_count
    if total == 0:
        return "🟡", 0

    score = (positive_count - negative_count) / total

    if score > 0.2:
        return "🟢", score
    elif score < -0.2:
        return "🔴", score
    else:
        return "🟡", score


def extract_price_movements(text: str) -> list:
    """從文字中提取股價漲跌幅"""
    import re
    movements = []
    # 匹配各種漲跌幅格式: up 5%, down 3%, +5%, -3%, 漲5%, 跌3%
    patterns = [
        r'(up|rise|gain|jump|surge|soar|climb)\s*(\d+(?:\.\d+)?)\s*%',
        r'(down|fall|drop|decline|plunge|tumble|sink)\s*(\d+(?:\.\d+)?)\s*%',
        r'[+＋](\d+(?:\.\d+)?)\s*%',
        r'[-－](\d+(?:\.\d+)?)\s*%',
        r'漲\s*(\d+(?:\.\d+)?)\s*%',
        r'跌\s*(\d+(?:\.\d+)?)\s*%',
        r'(\d+(?:\.\d+)?)\s*%\s*(higher|lower|up|down)',
    ]
    for pattern in patterns:
        matches = re.findall(pattern, text.lower())
        movements.extend(matches)
    return movements[:3]  # 最多返回3個


def extract_companies(text: str, category: str) -> list:
    """根據分類提取相關公司名稱"""

    # 各產業類別對應的公司（只顯示該產業相關公司）
    category_companies = {
        # 產業板塊
        "半導體": [
            ("nvidia", "NVIDIA"), ("nvda", "NVIDIA"), ("輝達", "NVIDIA"),
            ("台積電", "台積電"), ("tsmc", "台積電"),
            ("聯發科", "聯發科"), ("mediatek", "聯發科"),
            ("amd", "AMD"), ("intel", "Intel"), ("qualcomm", "高通"),
            ("broadcom", "Broadcom"), ("博通", "Broadcom"),
            ("asml", "ASML"), ("艾司摩爾", "ASML"),
            ("micron", "Micron"), ("美光", "Micron"),
            ("sk hynix", "SK海力士"), ("海力士", "SK海力士"),
            ("samsung", "三星"), ("三星", "三星"),
        ],
        "軟體/雲端": [
            ("microsoft", "Microsoft"), ("msft", "Microsoft"), ("微軟", "Microsoft"),
            ("salesforce", "Salesforce"), ("snowflake", "Snowflake"),
            ("servicenow", "ServiceNow"), ("crowdstrike", "CrowdStrike"),
            ("datadog", "Datadog"), ("mongodb", "MongoDB"),
            ("adobe", "Adobe"), ("oracle", "Oracle"),
        ],
        "網路/社群": [
            ("meta", "Meta"), ("facebook", "Meta"),
            ("alphabet", "Google"), ("googl", "Google"), ("google", "Google"),
            ("netflix", "Netflix"), ("spotify", "Spotify"),
            ("snap", "Snap"), ("pinterest", "Pinterest"),
        ],
        "硬體/消費電子": [
            ("apple", "Apple"), ("aapl", "Apple"), ("蘋果", "Apple"),
            ("samsung", "三星"), ("三星", "三星"),
            ("sony", "Sony"), ("lg", "LG"),
            ("鴻海", "鴻海"), ("foxconn", "鴻海"),
            ("和碩", "和碩"), ("pegatron", "和碩"),
        ],
        "AI人工智慧": [
            ("nvidia", "NVIDIA"), ("nvda", "NVIDIA"), ("輝達", "NVIDIA"),
            ("openai", "OpenAI"), ("chatgpt", "OpenAI"), ("anthropic", "Anthropic"),
            ("microsoft", "Microsoft"), ("google", "Google"), ("meta", "Meta"),
            ("palantir", "Palantir"), ("pltr", "Palantir"),
        ],
        "金融": [
            ("jpmorgan", "JPMorgan"), ("jp morgan", "JPMorgan"),
            ("goldman sachs", "Goldman"), ("goldman", "Goldman"),
            ("morgan stanley", "Morgan Stanley"),
            ("bank of america", "美銀"), ("citigroup", "花旗"),
            ("berkshire", "Berkshire"), ("visa", "Visa"), ("mastercard", "Mastercard"),
        ],
        "醫療保健": [
            ("unitedhealth", "UnitedHealth"), ("pfizer", "輝瑞"),
            ("eli lilly", "禮來"), ("novo nordisk", "諾和諾德"),
            ("johnson & johnson", "J&J"), ("merck", "默克"),
            ("abbvie", "AbbVie"), ("moderna", "Moderna"),
        ],
        "能源": [
            ("exxon", "Exxon"), ("chevron", "Chevron"),
            ("conocophillips", "ConocoPhillips"), ("schlumberger", "Schlumberger"),
            ("台塑化", "台塑化"), ("中油", "中油"),
        ],
        "汽車": [
            ("tesla", "Tesla"), ("tsla", "Tesla"), ("特斯拉", "Tesla"),
            ("gm", "GM"), ("ford", "Ford"), ("toyota", "豐田"),
            ("byd", "比亞迪"), ("rivian", "Rivian"), ("lucid", "Lucid"),
        ],
        "零售/消費": [
            ("walmart", "Walmart"), ("amazon", "Amazon"), ("amzn", "Amazon"),
            ("costco", "Costco"), ("target", "Target"),
            ("home depot", "Home Depot"), ("starbucks", "Starbucks"),
        ],
        "航空/運輸": [
            ("boeing", "Boeing"), ("airbus", "Airbus"),
            ("ups", "UPS"), ("fedex", "FedEx"),
            ("delta", "Delta"), ("united airlines", "United"),
            ("長榮航", "長榮航"), ("華航", "華航"),
        ],
        "通訊服務": [
            ("verizon", "Verizon"), ("at&t", "AT&T"), ("t-mobile", "T-Mobile"),
            ("comcast", "Comcast"), ("disney", "Disney"),
            ("中華電", "中華電"), ("台灣大", "台灣大"), ("遠傳", "遠傳"),
        ],
        "工業": [
            ("caterpillar", "Caterpillar"), ("cat", "Caterpillar"),
            ("deere", "Deere"), ("john deere", "Deere"),
            ("honeywell", "Honeywell"), ("general electric", "GE"), ("ge", "GE"),
            ("siemens", "Siemens"), ("3m", "3M"),
            ("lockheed", "Lockheed"), ("raytheon", "Raytheon"), ("northrop", "Northrop"),
            ("union pacific", "Union Pacific"), ("ups", "UPS"),
        ],
        "公用事業": [
            ("nextera", "NextEra"), ("duke energy", "Duke Energy"),
            ("southern company", "Southern Co"), ("dominion", "Dominion"),
            ("台電", "台電"),
        ],
        "基礎材料": [
            ("dow", "Dow"), ("basf", "BASF"), ("dupont", "DuPont"), ("linde", "Linde"),
            ("中鋼", "中鋼"), ("台塑", "台塑"), ("南亞", "南亞"),
            ("freeport", "Freeport"), ("newmont", "Newmont"),
            ("台泥", "台泥"), ("亞泥", "亞泥"),
        ],
        "鋼鐵/石化/水泥": [
            ("中鋼", "中鋼"), ("中鴻", "中鴻"), ("豐興", "豐興"),
            ("台塑", "台塑"), ("南亞", "南亞"), ("台化", "台化"), ("台塑化", "台塑化"),
            ("台泥", "台泥"), ("亞泥", "亞泥"),
            ("nucor", "Nucor"), ("us steel", "US Steel"),
        ],
        # 科技產業鏈
        "AI晶片": [
            ("nvidia", "NVIDIA"), ("nvda", "NVIDIA"), ("輝達", "NVIDIA"),
            ("amd", "AMD"), ("intel", "Intel"),
            ("google tpu", "Google TPU"), ("amazon trainium", "AWS"),
        ],
        "記憶體": [
            ("micron", "Micron"), ("美光", "Micron"),
            ("sk hynix", "SK海力士"), ("海力士", "SK海力士"),
            ("samsung", "三星"), ("南亞科", "南亞科"),
        ],
        "晶圓代工": [
            ("台積電", "台積電"), ("tsmc", "台積電"),
            ("globalfoundries", "GlobalFoundries"), ("聯電", "聯電"),
            ("samsung foundry", "三星"),
        ],
        "封測": [
            ("日月光", "日月光"), ("ase", "日月光"),
            ("矽品", "矽品"), ("京元電", "京元電"),
        ],
        "IC設計": [
            ("聯發科", "聯發科"), ("mediatek", "聯發科"),
            ("瑞昱", "瑞昱"), ("聯詠", "聯詠"), ("novatek", "聯詠"),
            ("高通", "高通"), ("qualcomm", "高通"), ("broadcom", "Broadcom"),
        ],
        "伺服器/資料中心": [
            ("supermicro", "Supermicro"), ("smci", "Supermicro"),
            ("廣達", "廣達"), ("quanta", "廣達"),
            ("緯創", "緯創"), ("wistron", "緯創"),
            ("緯穎", "緯穎"), ("英業達", "英業達"),
            ("dell", "Dell"), ("hpe", "HPE"),
        ],
        "網通設備": [
            ("cisco", "Cisco"), ("arista", "Arista"),
            ("juniper", "Juniper"), ("智邦", "智邦"),
        ],
        "PCB/散熱": [
            ("台郡", "台郡"), ("欣興", "欣興"), ("南電", "南電"),
            ("奇鋐", "奇鋐"), ("雙鴻", "雙鴻"),
        ],
        "電源供應": [
            ("台達電", "台達電"), ("delta", "台達電"),
            ("光寶", "光寶"), ("群光", "群光"),
        ],
        "面板/顯示": [
            ("友達", "友達"), ("auo", "友達"),
            ("群創", "群創"), ("innolux", "群創"),
            ("lg display", "LG Display"),
        ],
        "手機供應鏈": [
            ("鴻海", "鴻海"), ("foxconn", "鴻海"),
            ("和碩", "和碩"), ("pegatron", "和碩"),
            ("大立光", "大立光"), ("玉晶光", "玉晶光"),
        ],
        "AI應用/平台": [
            ("openai", "OpenAI"), ("anthropic", "Anthropic"),
            ("palantir", "Palantir"), ("c3.ai", "C3.ai"),
        ],
        "SaaS/雲服務": [
            ("salesforce", "Salesforce"), ("snowflake", "Snowflake"),
            ("servicenow", "ServiceNow"), ("workday", "Workday"),
            ("datadog", "Datadog"), ("mongodb", "MongoDB"),
        ],
        "科技巨頭": [
            ("microsoft", "Microsoft"), ("msft", "Microsoft"), ("微軟", "Microsoft"),
            ("meta", "Meta"), ("facebook", "Meta"),
            ("alphabet", "Google"), ("googl", "Google"), ("google", "Google"),
            ("amazon", "Amazon"), ("amzn", "Amazon"), ("亞馬遜", "Amazon"),
            ("apple", "Apple"), ("aapl", "Apple"), ("蘋果", "Apple"),
            ("nvidia", "NVIDIA"), ("nvda", "NVIDIA"), ("輝達", "NVIDIA"),
            ("tesla", "Tesla"), ("tsla", "Tesla"), ("特斯拉", "Tesla"),
        ],
        "AI基礎設施": [
            ("nvidia", "NVIDIA"), ("nvda", "NVIDIA"),
            ("supermicro", "Supermicro"), ("smci", "Supermicro"),
            ("廣達", "廣達"), ("緯創", "緯創"),
            ("arista", "Arista"), ("vertiv", "Vertiv"),
        ],
    }

    # 取得該類別的公司列表，如果沒有則使用通用列表
    company_patterns = category_companies.get(category, [])

    # 如果類別沒有特定公司列表，不提取公司名稱
    if not company_patterns:
        return []

    text_lower = text.lower()
    companies_found = []
    seen = set()
    for pattern, company in company_patterns:
        if pattern in text_lower and company not in seen:
            companies_found.append(company)
            seen.add(company)
            if len(companies_found) >= 3:
                break

    return companies_found


def extract_key_event(news_items: list) -> str:
    """從新聞中提取關鍵事件"""
    # 事件關鍵字（按優先順序排列）
    event_keywords = [
        # 重大事件優先
        ("layoff", "裁員"), ("cut job", "裁員"), ("job cut", "裁員"),
        ("plunge", "暴跌"), ("crash", "崩盤"), ("surge", "大漲"), ("soar", "飆漲"),
        ("record high", "創新高"), ("all-time high", "歷史新高"),
        # 財報相關
        ("earnings", "財報"), ("quarterly", "季報"), ("revenue", "營收"),
        ("profit", "獲利"), ("guidance", "財測"),
        ("beat", "優於預期"), ("miss", "不如預期"), ("disappoint", "令人失望"),
        # 公司動態
        ("acquire", "收購"), ("merger", "合併"), ("buyout", "併購"),
        ("ipo", "IPO"), ("split", "分拆"),
        ("launch", "發布新品"), ("unveil", "發表"), ("announce", "宣布"),
        ("partnership", "合作"), ("contract", "獲得合約"),
        # 評級變動
        ("upgrade", "上調評級"), ("downgrade", "下調評級"),
        ("price target", "目標價調整"),
        # AI/科技相關
        ("ai spending", "AI支出"), ("capex", "資本支出"),
        ("chip", "晶片"), ("semiconductor", "半導體"),
        # 政策/監管
        ("fda approv", "FDA核准"), ("antitrust", "反壟斷"),
        ("tariff", "關稅"), ("sanction", "制裁"), ("ban", "禁令"),
        # 經濟相關
        ("rate cut", "降息"), ("rate hike", "升息"),
        ("inflation", "通膨"), ("recession", "衰退"),
    ]

    for news in news_items[:5]:  # 檢查前5則
        text = (news["title"] + " " + (news["content"] or "")).lower()
        for keyword, event in event_keywords:
            if keyword in text:
                return event
    return ""


def generate_summary(category: str, news_items: list, sentiment: str) -> str:
    """根據新聞內容生成一句話總結，包含公司名稱、事件和漲跌幅"""
    if not news_items:
        return "今日無相關新聞"

    # 取得最重要的新聞標題（第一則）
    top_news = news_items[0]["title"]

    # 合併所有新聞文字
    text_all = " ".join([(n["title"] + " " + (n["content"] or "")).lower() for n in news_items])

    # 總經類別 - 不顯示公司名稱，直接使用模板
    MACRO_CATEGORIES = [
        "Fed/利率", "通膨", "GDP/經濟成長", "就業", "美元/匯率",
        "黃金/避險", "債券/殖利率", "貿易/關稅", "政府政策"
    ]

    # 如果是總經類別，跳過公司提取，直接進入模板邏輯
    if category not in MACRO_CATEGORIES:
        # 提取公司名稱（僅限產業和科技產業鏈類別）
        companies = extract_companies(text_all, category)

        # 提取關鍵事件
        event = extract_key_event(news_items)

        # 組合總結
        company_str = "、".join(companies[:2]) if companies else ""

        # 判斷漲跌方向
        up_keywords = ["up", "rise", "gain", "jump", "surge", "soar", "climb", "higher", "漲"]
        down_keywords = ["down", "fall", "drop", "decline", "plunge", "tumble", "sink", "lower", "跌"]

        is_up = any(kw in text_all for kw in up_keywords)
        is_down = any(kw in text_all for kw in down_keywords)

        # 生成智能總結（僅產業類別）
        if company_str and event:
            if is_up and not is_down:
                return f"{company_str}：{event}，股價走揚"
            elif is_down and not is_up:
                return f"{company_str}：{event}，股價承壓"
            else:
                return f"{company_str}：{event}"
        elif company_str:
            if is_up and not is_down:
                return f"{company_str} 相關消息正面，股價上漲"
            elif is_down and not is_up:
                return f"{company_str} 面臨壓力，股價下跌"
            else:
                return f"{company_str} 動態受關注"
        elif event:
            return f"產業{event}消息，影響市場情緒"

    # 總經類別使用專屬模板 - 區分「已宣布」vs「預期」

    # 判斷是否為已確認事件（使用過去式或確認性動詞）
    announced_words = ["holds", "held", "keeps", "kept", "announces", "announced",
                       "decides", "decided", "maintains", "maintained", "unchanged"]
    is_announced = any(w in text_all for w in announced_words)

    # Fed/利率
    if category == "Fed/利率":
        # 利率維持不變
        if any(w in text_all for w in ["hold", "steady", "unchanged", "pause"]):
            if is_announced:
                # 補充：Powell 繼任者相關新聞
                if "successor" in text_all or "replace" in text_all or "candidate" in text_all:
                    return "Fed 宣布維持利率不變；市場關注 Powell 繼任者人選"
                return "Fed 宣布維持利率不變，暫停降息步調"
            else:
                return "市場預期 Fed 將維持利率不變"
        # 降息
        elif "cut" in text_all:
            if is_announced or "cuts" in text_all:
                return "Fed 宣布降息，寬鬆政策啟動"
            else:
                return "市場預期 Fed 將降息，風險資產可能受惠"
        # 升息
        elif "hike" in text_all or "raise" in text_all:
            if is_announced:
                return "Fed 宣布升息，緊縮政策延續"
            else:
                return "升息預期升溫，債券殖利率走高"
        else:
            return "Fed 政策動態，持續關注利率走向"

    # 通膨
    elif category == "通膨":
        if "ease" in text_all or "cool" in text_all or "slow" in text_all or "fell" in text_all:
            return "通膨數據降溫，有利於寬鬆政策預期"
        elif "rise" in text_all or "surge" in text_all or "hot" in text_all or "sticky" in text_all:
            return "通膨壓力仍存，可能延後降息時程"
        elif "cpi" in text_all or "pce" in text_all:
            return "通膨數據公布，關注物價趨勢"
        else:
            return "通膨相關消息，觀察物價走勢"

    # 就業
    elif category == "就業":
        if "strong" in text_all or "beats" in text_all or "added" in text_all:
            return "就業數據強勁，勞動市場仍具韌性"
        elif "layoff" in text_all or "layoffs" in text_all:
            return "企業裁員消息頻傳，就業市場面臨壓力"
        elif "jobless" in text_all or "unemployment" in text_all:
            if "rise" in text_all or "higher" in text_all:
                return "失業率上升，就業市場降溫"
            elif "fall" in text_all or "low" in text_all:
                return "失業率維持低檔，經濟基本面穩健"
        else:
            return "就業市場消息，留意勞動數據"

    # 美元/匯率
    elif category == "美元/匯率":
        if "weak" in text_all or "fall" in text_all or "drop" in text_all or "slip" in text_all:
            return "美元走弱，新興市場與大宗商品受惠"
        elif "strong" in text_all or "rise" in text_all or "surge" in text_all:
            return "美元走強，出口企業與新興市場承壓"
        elif "intervention" in text_all:
            return "匯市干預消息，波動加劇"
        else:
            return "匯率市場波動，關注美元走勢"

    # 黃金/避險
    elif category == "黃金/避險":
        if "record" in text_all or "all-time" in text_all:
            return "黃金創歷史新高，避險需求強勁"
        elif "surge" in text_all or "jump" in text_all or "rally" in text_all:
            return "黃金大漲，避險情緒升溫"
        elif "fall" in text_all or "drop" in text_all or "retreat" in text_all:
            return "黃金回落，風險偏好回升"
        else:
            return "貴金屬市場波動，觀察避險情緒"

    # 貿易/關稅
    elif category == "貿易/關稅":
        if "tariff" in text_all:
            if "impose" in text_all or "announces" in text_all or "slaps" in text_all:
                return "關稅政策實施，貿易摩擦升級"
            elif "threat" in text_all or "warns" in text_all or "considers" in text_all:
                return "關稅威脅升溫，市場關注後續發展"
            elif "delay" in text_all or "pause" in text_all:
                return "關稅暫緩，市場鬆一口氣"
        elif "deal" in text_all or "agreement" in text_all:
            return "貿易協議進展，市場情緒改善"
        else:
            return "貿易政策動態，留意關稅發展"

    # 政府政策
    elif category == "政府政策":
        if "shutdown" in text_all:
            return "政府關門風險升高，市場不確定性增加"
        elif "stimulus" in text_all or "spending" in text_all:
            return "財政刺激政策動向，關注經濟影響"
        elif "debt ceiling" in text_all or "debt limit" in text_all:
            return "債務上限議題受關注，市場觀望"
        else:
            return "政府政策動態，關注財政走向"

    # 債券
    elif category == "債券/殖利率":
        if "invert" in text_all or "inverted" in text_all:
            return "殖利率曲線倒掛，衰退擔憂升溫"
        elif "rise" in text_all or "surge" in text_all or "climb" in text_all or "jump" in text_all:
            return "殖利率上升，債券價格承壓"
        elif "fall" in text_all or "drop" in text_all or "retreat" in text_all:
            return "殖利率下滑，資金流向避險資產"
        else:
            return "債券市場消息，留意殖利率變化"

    # GDP/經濟成長
    elif category == "GDP/經濟成長":
        if "recession" in text_all or "contract" in text_all:
            return "經濟衰退疑慮升溫，防禦性資產受青睞"
        elif "growth" in text_all or "expand" in text_all:
            return "經濟成長穩健，支撐企業獲利預期"
        else:
            return "經濟數據更新，觀察成長動能"

    # 科技/AI
    elif category == "科技/AI":
        if "spend" in text_all or "invest" in text_all:
            return "AI 投資熱潮持續，科技股受關注"
        elif "layoff" in text_all or "cut" in text_all:
            return "科技業裁員消息頻傳，成本控管為重點"
        elif "earn" in text_all:
            return "科技巨頭財報週，AI 支出成焦點"
        else:
            return "科技產業消息，關注 AI 與雲端發展"

    # 醫療保健
    elif category == "醫療保健":
        if "plunge" in text_all or "drop" in text_all or "fall" in text_all:
            return "醫療股重挫，政策風險衝擊估值"
        elif "fda" in text_all or "approv" in text_all:
            return "FDA 審批動態，藥廠股價波動"
        else:
            return "醫療產業消息，關注政策與新藥進展"

    # 汽車
    elif category == "汽車":
        if "ev" in text_all or "electric" in text_all:
            if "slow" in text_all or "cut" in text_all or "pullback" in text_all:
                return "電動車需求放緩，車廠調整策略"
            else:
                return "電動車產業動態，競爭格局變化"
        elif "tariff" in text_all:
            return "汽車業面臨關稅壓力，成本上升"
        else:
            return "汽車產業消息，關注電動車發展"

    # 航空/運輸
    elif category == "航空/運輸":
        if "layoff" in text_all or "cut" in text_all:
            return "物流業調整人力，反映需求變化"
        elif "earn" in text_all:
            return "運輸業財報公布，關注營運展望"
        else:
            return "運輸產業消息，留意物流與航運趨勢"

    # 金融/銀行
    elif category == "金融/銀行":
        if "earn" in text_all:
            return "銀行財報季，關注淨利差與信貸品質"
        else:
            return "金融產業消息，關注銀行財報與利差"

    # 能源
    elif category == "能源":
        if "oil" in text_all and ("rise" in text_all or "surge" in text_all):
            return "油價上漲，能源股受惠"
        elif "oil" in text_all and ("fall" in text_all or "drop" in text_all):
            return "油價下跌，通膨壓力緩解"
        else:
            return "能源產業消息，關注油價走勢"

    # 零售/消費
    elif category == "零售/消費":
        if "spend" in text_all and ("strong" in text_all or "rise" in text_all):
            return "消費支出強勁，零售股表現可期"
        elif "weak" in text_all or "slow" in text_all:
            return "消費動能放緩，零售業承壓"
        else:
            return "零售消費消息，觀察消費者信心"

    # 房地產
    elif category == "房地產":
        if "mortgage" in text_all and "rate" in text_all:
            return "房貸利率變動，影響購屋需求"
        else:
            return "房地產消息，關注房貸利率影響"

    # 加密貨幣
    elif category == "加密貨幣":
        if "surge" in text_all or "rally" in text_all or "rise" in text_all:
            return "加密貨幣上漲，市場風險偏好回升"
        elif "fall" in text_all or "drop" in text_all:
            return "加密貨幣回落，投資人轉趨保守"
        else:
            return "加密貨幣市場波動，觀察市場情緒"

    # 預設
    return "相關消息更新，持續關注後續發展"


def extract_specific_details(text: str, news_items: list) -> dict:
    """
    從新聞文字中提取具體細節（人名、數字、日期等）
    """
    import re
    details = {
        "people": [],
        "percentages": [],
        "countries": [],
        "companies": [],
        "dates": [],
        "amounts": [],
    }

    # 提取人名（常見金融人物）
    people_patterns = [
        (r"(kevin\s+warsh|warsh)", "Kevin Warsh (華許)"),
        (r"(jerome\s+powell|powell|鮑爾)", "Jerome Powell (鮑爾)"),
        (r"(jensen\s+huang|黃仁勳)", "黃仁勳 (Jensen Huang)"),
        (r"(trump|川普)", "川普"),
        (r"(elon\s+musk|馬斯克)", "Elon Musk"),
        (r"(魏哲家|c\.c\.\s*wei)", "魏哲家"),
        (r"(劉德音)", "劉德音"),
        (r"(蘇姿丰|lisa\s+su)", "蘇姿丰 (Lisa Su)"),
    ]
    for pattern, name in people_patterns:
        if re.search(pattern, text, re.IGNORECASE):
            if name not in details["people"]:
                details["people"].append(name)

    # 提取百分比
    pct_matches = re.findall(r'(\d+(?:\.\d+)?)\s*%', text)
    details["percentages"] = list(set(pct_matches))

    # 提取金額（億、兆）
    amount_patterns = [
        (r'(\d+(?:\.\d+)?)\s*兆', "兆"),
        (r'(\d+(?:\.\d+)?)\s*億', "億"),
        (r'\$(\d+(?:\.\d+)?)\s*(trillion|billion|million)', None),
    ]
    for pattern, unit in amount_patterns:
        matches = re.findall(pattern, text, re.IGNORECASE)
        if matches and unit:
            for m in matches:
                details["amounts"].append(f"{m}{unit}")

    # 提取國家（關稅相關）
    country_patterns = [
        (r"(china|中國|大陸)", "中國"),
        (r"(canada|加拿大)", "加拿大"),
        (r"(mexico|墨西哥)", "墨西哥"),
        (r"(taiwan|台灣)", "台灣"),
        (r"(japan|日本)", "日本"),
        (r"(eu|european|歐盟|歐洲)", "歐盟"),
    ]
    for pattern, name in country_patterns:
        if re.search(pattern, text, re.IGNORECASE):
            if name not in details["countries"]:
                details["countries"].append(name)

    # 提取公司名稱
    company_patterns = [
        (r"(nvidia|輝達)", "NVIDIA"),
        (r"(tsmc|台積電)", "台積電"),
        (r"(apple|蘋果)", "Apple"),
        (r"(microsoft|微軟)", "Microsoft"),
        (r"(google|alphabet|谷歌)", "Google"),
        (r"(amazon|亞馬遜)", "Amazon"),
        (r"(meta|臉書)", "Meta"),
        (r"(tesla|特斯拉)", "Tesla"),
        (r"(broadcom|博通)", "Broadcom"),
        (r"(amd|超微)", "AMD"),
    ]
    for pattern, name in company_patterns:
        if re.search(pattern, text, re.IGNORECASE):
            if name not in details["companies"]:
                details["companies"].append(name)

    # 提取月份/日期
    date_patterns = [
        (r"(january|一月|1月)", "1月"),
        (r"(february|二月|2月)", "2月"),
        (r"(march|三月|3月)", "3月"),
        (r"(april|四月|4月)", "4月"),
        (r"(may|五月|5月)", "5月"),
        (r"(june|六月|6月)", "6月"),
        (r"(july|七月|7月)", "7月"),
        (r"(august|八月|8月)", "8月"),
        (r"(september|九月|9月)", "9月"),
        (r"(october|十月|10月)", "10月"),
        (r"(november|十一月|11月)", "11月"),
        (r"(december|十二月|12月)", "12月"),
    ]
    for pattern, name in date_patterns:
        if re.search(pattern, text, re.IGNORECASE):
            if name not in details["dates"]:
                details["dates"].append(name)

    return details


def generate_dual_summary(category: str, news_items: list) -> dict:
    """
    生成雙欄總結：確認事實 + 市場預期
    Returns: {"facts": str, "expectations": str}
    """
    if not news_items:
        return {"facts": "—", "expectations": "—"}

    # 合併所有新聞文字
    text_all = " ".join([(n["title"] + " " + (n["content"] or "")).lower() for n in news_items])
    text_original = " ".join([(n["title"] + " " + (n["content"] or "")) for n in news_items])
    top_news = news_items[0]["title"]

    # 提取具體細節
    details = extract_specific_details(text_original, news_items)

    # 事實判斷詞（過去式、已確認）
    fact_words = ["holds", "held", "keeps", "kept", "announces", "announced",
                  "decides", "decided", "maintains", "maintained", "unchanged",
                  "cuts", "raises", "raised", "rose", "fell", "dropped", "jumped",
                  "reported", "posted", "beat", "missed", "surged", "plunged"]

    # 預期判斷詞
    expect_words = ["expected", "expects", "may", "might", "could", "likely",
                    "forecast", "predict", "anticipate", "outlook", "guidance",
                    "will", "would", "should", "plan", "plans", "consider"]

    has_facts = any(w in text_all for w in fact_words)
    has_expectations = any(w in text_all for w in expect_words)

    facts = "—"
    expectations = "—"

    # ===== Fed/利率 =====
    if category == "Fed/利率":
        fact_parts = []

        # 事實：利率決策
        if any(w in text_all for w in ["holds", "held", "keeps", "kept", "maintains", "maintained"]):
            if "rate" in text_all or "interest" in text_all:
                fact_parts.append("Fed 宣布維持利率不變")
        elif "cut" in text_all and any(w in text_all for w in ["announced", "cuts", "decided"]):
            fact_parts.append("Fed 宣布降息")
        elif "hike" in text_all or "raise" in text_all:
            if any(w in text_all for w in ["announced", "raises", "raised"]):
                fact_parts.append("Fed 宣布升息")

        # 附加事實：Fed 主席繼任者
        if "warsh" in text_all or "華許" in text_all:
            if "nominate" in text_all or "提名" in text_all or "successor" in text_all:
                nominee_info = "川普提名 Kevin Warsh (華許) 接任 Fed 主席"
                if "5月" in text_original or "may" in text_all:
                    nominee_info += "，預計5月鮑爾任期屆滿後接任"
                fact_parts.append(nominee_info)
        elif "successor" in text_all or "replace" in text_all or "candidate" in text_all or "繼任" in text_all:
            fact_parts.append("Powell 繼任者議題浮現")

        # 組合事實
        if fact_parts:
            facts = "；".join(fact_parts)

        # 預期
        if "pause" in text_all or "wait" in text_all:
            expectations = "市場預期短期維持觀望"
        elif "cut" in text_all and any(w in text_all for w in expect_words):
            expectations = "市場預期未來可能降息"
        elif "hike" in text_all and any(w in text_all for w in expect_words):
            expectations = "市場預期可能再升息"
        elif "data" in text_all or "inflation" in text_all:
            expectations = "關注後續經濟數據走向"

    # ===== 通膨 =====
    elif category == "通膨":
        if "cpi" in text_all or "pce" in text_all:
            if "fell" in text_all or "dropped" in text_all or "eased" in text_all:
                facts = "通膨數據下滑"
            elif "rose" in text_all or "jumped" in text_all or "higher" in text_all:
                facts = "通膨數據上升"
            elif "reported" in text_all or "released" in text_all:
                facts = "通膨數據公布"

        if "sticky" in text_all or "persistent" in text_all:
            expectations = "通膨黏性仍高，降息時程恐延後"
        elif "ease" in text_all or "cool" in text_all:
            expectations = "通膨有望持續降溫"
        elif "target" in text_all:
            expectations = "關注是否達成 2% 目標"

    # ===== 就業 =====
    elif category == "就業":
        if "added" in text_all or "payroll" in text_all:
            if "beat" in text_all or "strong" in text_all:
                facts = "非農就業數據優於預期"
            elif "miss" in text_all or "weak" in text_all:
                facts = "非農就業數據不如預期"
            else:
                facts = "非農就業數據公布"
        elif "layoff" in text_all or "layoffs" in text_all:
            facts = "企業裁員消息頻傳"
        elif "unemployment" in text_all:
            if "rose" in text_all or "higher" in text_all:
                facts = "失業率上升"
            elif "fell" in text_all or "low" in text_all:
                facts = "失業率維持低檔"

        if "recession" in text_all:
            expectations = "就業惡化恐加深衰退擔憂"
        elif "soft landing" in text_all:
            expectations = "軟著陸預期仍存"
        elif "labor" in text_all and "tight" in text_all:
            expectations = "勞動市場仍偏緊俏"

    # ===== 貿易/關稅 =====
    elif category == "貿易/關稅":
        import re
        fact_parts = []

        # 提取國家與關稅百分比的配對
        tariff_details = []
        country_tariff_patterns = [
            (r"china|中國|大陸", "中國"),
            (r"canada|加拿大", "加拿大"),
            (r"mexico|墨西哥", "墨西哥"),
            (r"eu|european|歐盟|歐洲", "歐盟"),
            (r"japan|日本", "日本"),
            (r"taiwan|台灣", "台灣"),
        ]

        # 嘗試提取「國家 + 百分比」的關稅資訊
        for pattern, country_name in country_tariff_patterns:
            # 搜尋該國家附近的百分比
            country_match = re.search(pattern, text_all, re.IGNORECASE)
            if country_match:
                # 在國家名稱前後 50 字元範圍內搜尋百分比
                start = max(0, country_match.start() - 50)
                end = min(len(text_all), country_match.end() + 50)
                context = text_all[start:end]
                pct_match = re.search(r'(\d+(?:\.\d+)?)\s*%', context)
                if pct_match:
                    tariff_details.append(f"{country_name} {pct_match.group(1)}%")

        # 組合關稅細節
        if tariff_details:
            fact_parts.append("關稅現況：" + "、".join(tariff_details))
        elif "impose" in text_all or "slaps" in text_all or "enacted" in text_all:
            fact_parts.append("關稅政策已實施")
        elif "announced" in text_all and "tariff" in text_all:
            fact_parts.append("關稅措施宣布")
        elif "delay" in text_all or "pause" in text_all:
            fact_parts.append("關稅措施暫緩")

        # 加入涉及的國家列表（如果沒有具體稅率）
        if not tariff_details and details["countries"]:
            fact_parts.append(f"涉及國家：{', '.join(details['countries'])}")

        if fact_parts:
            facts = "；".join(fact_parts)

        # 預期
        if "threat" in text_all or "warns" in text_all:
            expectations = "更多關稅威脅可能出現"
        elif "negotiat" in text_all or "talk" in text_all:
            expectations = "貿易談判持續進行中"
        elif "retaliat" in text_all:
            expectations = "留意對方報復措施"
        elif "escalat" in text_all:
            expectations = "貿易戰可能升級"

    # ===== AI/科技 =====
    elif category == "AI/科技" or category == "科技" or category == "AI":
        fact_parts = []

        # 黃仁勳/NVIDIA 相關
        if "黃仁勳" in text_original or "jensen huang" in text_all:
            event_desc = "黃仁勳 (Jensen Huang)"
            if "宴" in text_original or "dinner" in text_all or "banquet" in text_all:
                event_desc = "黃仁勳舉辦兆元宴"
                # 檢查與會者
                attendees = []
                if "魏哲家" in text_original or "c.c. wei" in text_all:
                    attendees.append("台積電魏哲家")
                if "劉德音" in text_original:
                    attendees.append("劉德音")
                if "供應鏈" in text_original or "supply chain" in text_all:
                    event_desc += "，供應鏈大老齊聚"
                elif attendees:
                    event_desc += f"，{', '.join(attendees)}等出席"
            elif "台北" in text_original or "taipei" in text_all:
                event_desc += " 訪台"
            fact_parts.append(event_desc)

        # NVIDIA 財報/業績
        if "nvidia" in text_all or "輝達" in text_original:
            if "earnings" in text_all or "財報" in text_original:
                if "beat" in text_all or "超預期" in text_original:
                    fact_parts.append("NVIDIA 財報優於預期")
                elif "miss" in text_all:
                    fact_parts.append("NVIDIA 財報不如預期")
                else:
                    fact_parts.append("NVIDIA 財報發布")
            if "guidance" in text_all or "財測" in text_original:
                fact_parts.append("NVIDIA 發布財測指引")

        # AI 產業動態
        if "ai chip" in text_all or "ai 晶片" in text_original or "人工智慧晶片" in text_original:
            fact_parts.append("AI 晶片需求相關消息")
        if "data center" in text_all or "資料中心" in text_original:
            fact_parts.append("資料中心需求持續")

        # 其他科技公司
        if details["companies"]:
            companies_mentioned = [c for c in details["companies"] if c != "NVIDIA"]
            if companies_mentioned and not fact_parts:
                fact_parts.append(f"相關公司：{', '.join(companies_mentioned[:3])}")

        if fact_parts:
            facts = "；".join(fact_parts)
        else:
            facts = "AI/科技產業動態更新"

        # 預期
        if "demand" in text_all or "需求" in text_original:
            expectations = "AI 相關需求持續看好"
        elif "competition" in text_all or "競爭" in text_original:
            expectations = "關注產業競爭態勢"
        elif "supply" in text_all or "供應" in text_original:
            expectations = "供應鏈狀況受關注"
        else:
            expectations = "持續關注 AI 產業發展"

    # ===== 黃金/避險 =====
    elif category == "黃金/避險":
        if "record" in text_all or "all-time" in text_all:
            facts = "黃金創歷史新高"
        elif "surged" in text_all or "jumped" in text_all or "rallied" in text_all:
            facts = "黃金大幅上漲"
        elif "fell" in text_all or "dropped" in text_all:
            facts = "黃金價格回落"

        if "safe haven" in text_all or "geopolitical" in text_all:
            expectations = "避險需求可能持續"
        elif "dollar" in text_all:
            expectations = "關注美元走勢影響"

    # ===== 債券/殖利率 =====
    elif category == "債券/殖利率":
        if "invert" in text_all:
            facts = "殖利率曲線倒掛"
        elif "rose" in text_all or "jumped" in text_all or "climbed" in text_all:
            facts = "殖利率上升"
        elif "fell" in text_all or "dropped" in text_all:
            facts = "殖利率下滑"

        if "recession" in text_all:
            expectations = "倒掛加深衰退擔憂"
        elif "fed" in text_all:
            expectations = "關注 Fed 政策影響"

    # ===== 美元/匯率 =====
    elif category == "美元/匯率":
        if "rose" in text_all or "strengthened" in text_all or "surged" in text_all:
            facts = "美元走強"
        elif "fell" in text_all or "weakened" in text_all or "dropped" in text_all:
            facts = "美元走弱"
        elif "intervention" in text_all:
            facts = "央行干預匯市"

        if "emerging" in text_all:
            expectations = "新興市場可能承壓"
        elif "export" in text_all:
            expectations = "出口企業受匯率影響"

    # ===== GDP/經濟成長 =====
    elif category == "GDP/經濟成長":
        if "grew" in text_all or "expanded" in text_all:
            facts = "GDP 正成長"
        elif "contracted" in text_all or "shrank" in text_all:
            facts = "GDP 負成長"
        elif "reported" in text_all or "released" in text_all:
            facts = "GDP 數據公布"

        if "recession" in text_all:
            expectations = "衰退風險受關注"
        elif "soft landing" in text_all:
            expectations = "軟著陸預期"
        elif "growth" in text_all and any(w in text_all for w in expect_words):
            expectations = "經濟成長展望審慎"

    # ===== 政府政策 =====
    elif category == "政府政策":
        if "shutdown" in text_all:
            if "avoid" in text_all or "avert" in text_all:
                facts = "政府關門危機暫解"
            else:
                facts = "政府關門風險升高"
        elif "pass" in text_all or "approved" in text_all:
            facts = "政策法案通過"

        if "debt" in text_all and "ceiling" in text_all:
            expectations = "債務上限議題待解"
        elif "stimulus" in text_all:
            expectations = "財政刺激政策動向"

    # ===== 個股/企業 =====
    elif category == "個股/企業" or category == "企業" or category == "個股":
        fact_parts = []

        # 財報相關
        if "earnings" in text_all or "財報" in text_original:
            if "beat" in text_all or "超預期" in text_original or "優於" in text_original:
                fact_parts.append("財報優於預期")
            elif "miss" in text_all or "不如預期" in text_original:
                fact_parts.append("財報不如預期")
            else:
                fact_parts.append("財報公布")

        # 人事/活動
        if details["people"]:
            people_str = "、".join(details["people"][:3])
            if "宴" in text_original or "dinner" in text_all or "banquet" in text_all:
                fact_parts.append(f"{people_str}舉辦餐會活動")
            elif "訪" in text_original or "visit" in text_all:
                fact_parts.append(f"{people_str}出訪活動")
            elif "會議" in text_original or "meeting" in text_all:
                fact_parts.append(f"{people_str}參與會議")

        # 公司動態
        if details["companies"]:
            companies_str = "、".join(details["companies"][:3])
            if not fact_parts:
                fact_parts.append(f"涉及公司：{companies_str}")

        # 金額相關
        if details["amounts"]:
            amounts_str = "、".join(details["amounts"][:2])
            fact_parts.append(f"涉及金額：{amounts_str}")

        if fact_parts:
            facts = "；".join(fact_parts)
        else:
            facts = "企業動態更新"

        # 預期
        if "guidance" in text_all or "財測" in text_original:
            expectations = "關注後續財測指引"
        elif "merger" in text_all or "acquisition" in text_all or "併購" in text_original:
            expectations = "併購案後續發展"
        elif "layoff" in text_all or "裁員" in text_original:
            expectations = "關注企業營運狀況"
        else:
            expectations = "持續關注企業動態"

    # ===== 通用處理（確保輸出中文）=====
    # 各類別的預設中文描述
    category_default_facts = {
        "Fed/利率": "Fed 政策動態更新",
        "通膨": "通膨相關數據發布",
        "就業": "就業市場消息更新",
        "貿易/關稅": "貿易政策動態",
        "黃金/避險": "貴金屬市場波動",
        "債券/殖利率": "債市行情變化",
        "美元/匯率": "匯率市場動態",
        "GDP/經濟成長": "經濟數據更新",
        "政府政策": "政府政策動態",
        "AI/科技": "AI/科技產業動態",
        "科技": "科技產業動態",
        "AI": "人工智慧產業動態",
        "個股/企業": "企業動態更新",
        "企業": "企業動態更新",
        "個股": "個股動態更新",
    }

    category_default_expectations = {
        "Fed/利率": "持續關注利率政策走向",
        "通膨": "觀察通膨趨勢變化",
        "就業": "留意勞動市場表現",
        "貿易/關稅": "關注後續貿易發展",
        "黃金/避險": "觀察避險情緒變化",
        "債券/殖利率": "留意殖利率走勢",
        "美元/匯率": "關注匯率波動影響",
        "GDP/經濟成長": "觀察經濟成長動能",
        "政府政策": "關注政策後續發展",
        "AI/科技": "持續關注 AI 產業發展",
        "科技": "持續關注科技產業發展",
        "AI": "持續關注 AI 產業發展",
        "個股/企業": "持續關注企業動態",
        "企業": "持續關注企業動態",
        "個股": "持續關注個股表現",
    }

    # 如果沒有匹配到具體事實，使用類別預設
    if facts == "—" and has_facts:
        facts = category_default_facts.get(category, "相關消息更新")

    # 如果沒有匹配到具體預期，使用類別預設
    if expectations == "—" and has_expectations:
        if "outlook" in text_all or "guidance" in text_all:
            expectations = "關注後續財測展望"
        elif "earnings" in text_all:
            expectations = "財報季持續關注"
        else:
            expectations = category_default_expectations.get(category, "持續觀察後續發展")

    # 限制文字長度避免破版（最多 60 個字元）
    max_len = 60
    if len(facts) > max_len:
        facts = facts[:max_len-1] + "…"
    if len(expectations) > max_len:
        expectations = expectations[:max_len-1] + "…"

    return {"facts": facts, "expectations": expectations}


@st.cache_resource
def get_connection():
    """取得新聞資料庫連接 (SQLite fallback)"""
    if DB_TYPE != "sqlite":
        return None  # 非 SQLite 不需要連接物件
    if not DB_PATH.exists():
        raise FileNotFoundError(f"新聞資料庫不存在: {DB_PATH}")
    return sqlite3.connect(DB_PATH, check_same_thread=False)


@st.cache_resource
def get_finance_connection():
    """取得金融資料庫連接 (SQLite fallback)"""
    if DB_TYPE != "sqlite":
        return None  # 非 SQLite 不需要連接物件
    if not FINANCE_DB_PATH.exists():
        raise FileNotFoundError(f"金融資料庫不存在: {FINANCE_DB_PATH}")
    return sqlite3.connect(FINANCE_DB_PATH, check_same_thread=False)


def get_watchlist():
    """取得追蹤清單 - 使用統一資料層"""
    try:
        client = _get_data_client()
        data = client.get_watchlist()
        return [{"symbol": r["symbol"], "name": r.get("name", ""), "market": r.get("market", ""),
                 "sector": r.get("sector", ""), "industry": r.get("industry", ""), "description": ""} for r in data]
    except Exception as e:
        st.error(f"取得追蹤清單失敗: {e}")
        return []


def get_stock_info(symbol: str):
    """取得單一股票的詳細資訊 - 使用統一資料層"""
    try:
        client = _get_data_client()
        watchlist = client.get_watchlist()
        for r in watchlist:
            if r.get("symbol") == symbol:
                return {"symbol": r["symbol"], "name": r.get("name", ""), "market": r.get("market", ""),
                        "sector": r.get("sector", ""), "industry": r.get("industry", ""), "description": ""}
        return None
    except Exception:
        return None


def get_stock_prices(symbol: str, start_date: date = None, end_date: date = None):
    """取得股票價格數據 - 使用統一資料層"""
    try:
        client = _get_data_client()
        data = client.get_daily_prices(symbol, start_date=start_date, end_date=end_date)

        if data:
            df = pd.DataFrame(data)
            if "date" in df.columns:
                df["date"] = pd.to_datetime(df["date"])
                # 按日期升序排列
                df = df.sort_values("date").reset_index(drop=True)
            return df
        return pd.DataFrame()
    except Exception as e:
        st.error(f"取得價格數據失敗: {e}")
        return pd.DataFrame()


def get_stock_fundamentals(symbol: str):
    """取得股票基本面數據"""
    # 目前統一資料層尚未支援 fundamentals 查詢
    # 當使用 PostgreSQL 時暫時返回 None
    if DB_TYPE != "sqlite":
        return None

    conn = get_finance_connection()
    if not conn:
        return None
    cursor = conn.cursor()
    cursor.execute("""
        SELECT * FROM fundamentals
        WHERE symbol = ?
        ORDER BY date DESC
        LIMIT 1
    """, (symbol,))

    row = cursor.fetchone()
    if row:
        columns = [desc[0] for desc in cursor.description]
        return dict(zip(columns, row))
    return None


def get_news_for_stock(symbol: str, selected_date: date):
    """取得與股票相關的新聞 - 使用統一資料層"""
    # 建立搜尋關鍵字
    symbol_clean = symbol.replace(".TW", "").replace("^", "")

    # 股票代碼對應的公司名稱
    stock_keywords = {
        "AAPL": ["apple", "iphone", "aapl"],
        "MSFT": ["microsoft", "msft", "azure", "windows"],
        "GOOGL": ["google", "alphabet", "googl", "android", "youtube"],
        "AMZN": ["amazon", "amzn", "aws"],
        "NVDA": ["nvidia", "nvda", "gpu", "chip"],
        "META": ["meta", "facebook", "instagram", "whatsapp"],
        "TSLA": ["tesla", "tsla", "elon musk", "ev"],
        "JPM": ["jpmorgan", "jp morgan", "jpm", "jamie dimon"],
        "V": ["visa"],
        "UNH": ["unitedhealth", "unh"],
        "2330": ["tsmc", "台積電", "2330"],
        "2317": ["鴻海", "foxconn", "hon hai", "2317"],
        "2454": ["聯發科", "mediatek", "2454"],
        "SPY": ["s&p 500", "s&p500", "spy"],
        "QQQ": ["nasdaq", "qqq", "nasdaq 100"],
    }

    keywords = stock_keywords.get(symbol_clean, [symbol_clean.lower()])

    try:
        # 取得當天新聞，然後在 Python 中過濾關鍵字
        news_list = get_news_by_date(selected_date)
        all_news = []

        for news in news_list:
            title_lower = (news.get("title") or "").lower()
            content_lower = (news.get("content") or "").lower()
            text = title_lower + " " + content_lower

            for keyword in keywords:
                if keyword.lower() in text:
                    all_news.append(news)
                    break

        # 去重
        seen_ids = set()
        unique_news = []
        for n in all_news:
            news_id = n.get("id")
            if news_id and news_id not in seen_ids:
                seen_ids.add(news_id)
                unique_news.append(n)

        return unique_news
    except Exception as e:
        return []


def get_news_in_date_range(start_date: date, end_date: date, keyword: str = None):
    """取得日期範圍內的新聞統計 - 使用統一資料層"""
    try:
        client = _get_data_client()
        news_list = client.get_news(
            start_date=start_date,
            end_date=end_date,
            limit=5000
        )

        date_counts = {}
        for r in news_list:
            # 過濾關鍵字
            if keyword and keyword.lower() not in (r.get("title") or "").lower():
                continue

            # 取得日期（優先使用 collected_at，fallback 到 published_at）
            date_val = r.get("collected_at") or r.get("published_at") or ""
            if date_val:
                d = str(date_val)[:10]
                date_counts[d] = date_counts.get(d, 0) + 1

        return date_counts
    except Exception as e:
        return {}


def get_available_dates():
    """取得有新聞的日期列表 - 使用統一資料層"""
    try:
        client = _get_data_client()
        # 取得最近 90 天的新聞來提取日期
        end_date = date.today()
        start_date = end_date - timedelta(days=90)
        news_list = client.get_news(start_date=start_date, end_date=end_date, limit=5000)

        dates_set = set()
        for r in news_list:
            date_val = r.get("collected_at") or r.get("published_at") or ""
            if date_val:
                dates_set.add(str(date_val)[:10])

        dates = sorted(dates_set, reverse=True)
        return [datetime.strptime(d, "%Y-%m-%d").date() for d in dates if d]
    except Exception as e:
        return []


def get_ptt_available_dates():
    """取得 PTT 有文章的日期列表 - 使用統一資料層"""
    try:
        client = _get_data_client()
        # 取得最近 90 天的 PTT 新聞
        end_date = date.today()
        start_date = end_date - timedelta(days=90)
        news_list = client.get_news(
            start_date=start_date, end_date=end_date,
            source="ptt", limit=5000
        )

        dates_set = set()
        for r in news_list:
            if r.get("source_type") == "ptt" and r.get("published_at"):
                dates_set.add(str(r["published_at"])[:10])

        dates = sorted(dates_set, reverse=True)
        return [datetime.strptime(d, "%Y-%m-%d").date() for d in dates if d]
    except Exception as e:
        return []


def get_news_by_date(selected_date: date):
    """取得指定日期的新聞 - 使用統一資料層"""
    try:
        client = _get_data_client()
        # 使用統一資料層的 get_news 方法
        news_list = client.get_news(
            start_date=selected_date,
            end_date=selected_date,
            limit=500
        )
        return news_list
    except Exception as e:
        st.error(f"取得新聞失敗: {e}")
        return []


def get_news_stats_by_date(selected_date: date):
    """取得指定日期的新聞統計 - 使用統一資料層"""
    try:
        # 取得當日新聞並計算統計
        news_list = get_news_by_date(selected_date)

        by_source_type = {}
        by_source = {}
        for r in news_list:
            st = r.get("source_type") or "other"
            by_source_type[st] = by_source_type.get(st, 0) + 1
            s = r.get("source") or "unknown"
            by_source[s] = by_source.get(s, 0) + 1

        return {
            "total_count": len(news_list),
            "by_source_type": by_source_type,
            "by_source": dict(sorted(by_source.items(), key=lambda x: x[1], reverse=True)[:10]),
        }
    except Exception as e:
        return {"total_count": 0, "by_source_type": {}, "by_source": {}}


def get_weekly_news(end_date: date, days: int = 7) -> list:
    """取得過去一週的新聞 - 使用統一資料層"""
    try:
        client = _get_data_client()
        start_date = end_date - timedelta(days=days)
        news_list = client.get_news(
            start_date=start_date,
            end_date=end_date,
            limit=2000
        )
        return news_list
    except Exception as e:
        st.error(f"取得週新聞失敗: {e}")
        return []


def categorize_news(news_list: list) -> dict:
    """將新聞分類為總經、產業和科技產業鏈"""
    macro_news = defaultdict(list)
    industry_news = defaultdict(list)
    tech_supply_chain_news = defaultdict(list)

    for news in news_list:
        title_lower = news["title"].lower()
        content_lower = (news["content"] or "").lower()
        text = title_lower + " " + content_lower

        # 總經分類
        for category, keywords in MACRO_KEYWORDS.items():
            if any(kw in text for kw in keywords):
                macro_news[category].append(news)
                break

        # 產業分類
        for category, keywords in INDUSTRY_KEYWORDS.items():
            if any(kw in text for kw in keywords):
                industry_news[category].append(news)
                break

        # 科技產業鏈分類（一則新聞可歸入多個產業鏈類別）
        for category, keywords in TECH_SUPPLY_CHAIN_KEYWORDS.items():
            if any(kw in text for kw in keywords):
                tech_supply_chain_news[category].append(news)

    return {
        "macro": dict(macro_news),
        "industry": dict(industry_news),
        "tech_supply_chain": dict(tech_supply_chain_news),
    }


def generate_weekly_summary(category: str, weekly_news: list, daily_count: int) -> tuple:
    """
    根據一週新聞生成產業總結
    Returns: (燈號, 總結文字, 週趨勢)
    """
    if not weekly_news:
        return "⚪", "本週無相關新聞", "—"

    # 合併所有新聞文字
    text_all = " ".join([(n["title"] + " " + (n["content"] or "")).lower() for n in weekly_news])

    # 計算正負面情緒
    positive_count = sum(1 for kw in POSITIVE_KEYWORDS if kw in text_all)
    negative_count = sum(1 for kw in NEGATIVE_KEYWORDS if kw in text_all)

    # 判斷週趨勢和燈號
    if positive_count > negative_count * 1.5:
        trend = "📈偏多"
        light = "🟢"
    elif negative_count > positive_count * 1.5:
        trend = "📉偏空"
        light = "🔴"
    else:
        trend = "➡️中性"
        light = "🟡"

    # 各產業的結論模板
    category_conclusions = {
        # 產業板塊
        "半導體": {
            "🟢": "晶片需求回溫，庫存去化順利，產業景氣回升",
            "🔴": "終端需求疲軟，庫存壓力仍在，短期承壓",
            "🟡": "景氣能見度不明，等待需求回升訊號",
        },
        "軟體/雲端": {
            "🟢": "企業IT支出成長，雲端轉型趨勢延續",
            "🔴": "企業縮減支出，成長動能放緩",
            "🟡": "支出態度保守，聚焦AI相關投資",
        },
        "網路/社群": {
            "🟢": "廣告市場復甦，用戶成長穩健",
            "🔴": "廣告支出收縮，競爭加劇",
            "🟡": "廣告市場分化，平台表現不一",
        },
        "硬體/消費電子": {
            "🟢": "消費需求回溫，新品帶動換機潮",
            "🔴": "消費力道疲弱，庫存調整中",
            "🟡": "需求平穩，等待新品週期啟動",
        },
        "AI人工智慧": {
            "🟢": "AI應用加速落地，投資熱度不減",
            "🔴": "AI變現疑慮浮現，估值面臨修正",
            "🟡": "AI發展持續，但投資回報待驗證",
        },
        "金融": {
            "🟢": "利差擴大、資產品質穩健，獲利成長",
            "🔴": "信用風險升溫，淨利差收窄",
            "🟡": "利率環境不確定，金融股觀望",
        },
        "醫療保健": {
            "🟢": "新藥進展順利，醫療需求穩定成長",
            "🔴": "藥價壓力、臨床失敗，產業承壓",
            "🟡": "防禦特性顯現，表現相對穩健",
        },
        "能源": {
            "🟢": "油價走強，能源股獲利改善",
            "🔴": "油價走弱，獲利面臨壓縮",
            "🟡": "油價震盪，關注OPEC政策動向",
        },
        "汽車": {
            "🟢": "車市需求回升，電動車滲透率提高",
            "🔴": "需求放緩，價格戰壓縮利潤",
            "🟡": "傳統車穩定，電動車競爭加劇",
        },
        "零售/消費": {
            "🟢": "消費信心回升，零售銷售成長",
            "🔴": "消費力道轉弱，庫存壓力上升",
            "🟡": "消費分化，必需品優於非必需品",
        },
        "航空/運輸": {
            "🟢": "旅遊需求強勁，運價維持高檔",
            "🔴": "需求放緩，運價走跌",
            "🟡": "運輸需求平穩，關注燃油成本",
        },
        "通訊服務": {
            "🟢": "5G用戶成長，ARPU提升",
            "🔴": "競爭激烈，用戶成長趨緩",
            "🟡": "產業成熟，股利殖利率具吸引力",
        },
        "工業": {
            "🟢": "製造業復甦，基建投資增加",
            "🔴": "訂單下滑，景氣循環向下",
            "🟡": "製造業持平，等待政策刺激",
        },
        "公用事業": {
            "🟢": "監管環境友善，電價調漲反映成本",
            "🔴": "利率上升增加融資成本",
            "🟡": "防禦特性顯現，適合避險配置",
        },
        "基礎材料": {
            "🟢": "原物料價格上漲，產業獲利改善",
            "🔴": "需求疲軟，原物料價格走跌",
            "🟡": "原物料價格震盪，關注中國需求",
        },
        "鋼鐵/石化/水泥": {
            "🟢": "營建需求回升，報價走揚",
            "🔴": "內需不振，報價持續走跌",
            "🟡": "傳產景氣平淡，等待需求回溫",
        },
        "房地產": {
            "🟢": "房市回溫，交易量增加",
            "🔴": "高利率衝擊，房市降溫",
            "🟡": "房市觀望，等待利率方向明朗",
        },
        "加密貨幣": {
            "🟢": "市場情緒樂觀，資金持續流入",
            "🔴": "監管疑慮、市場恐慌，價格下跌",
            "🟡": "價格盤整，等待突破方向",
        },
        # 科技產業鏈
        "AI晶片": {
            "🟢": "AI算力需求爆發，供不應求",
            "🔴": "需求成長疑慮，庫存風險浮現",
            "🟡": "需求維持高檔，但成長趨緩",
        },
        "記憶體": {
            "🟢": "HBM需求強勁，價格止跌回升",
            "🔴": "供過於求，價格持續下跌",
            "🟡": "傳統記憶體疲軟，HBM獨強",
        },
        "晶圓代工": {
            "🟢": "先進製程滿載，產能供不應求",
            "🔴": "稼動率下滑，價格面臨壓力",
            "🟡": "先進製程穩健，成熟製程調整",
        },
        "封測": {
            "🟢": "先進封裝需求強，產能吃緊",
            "🔴": "傳統封測需求弱，稼動率下滑",
            "🟡": "CoWoS產能擴充中，傳統封測持平",
        },
        "IC設計": {
            "🟢": "新品拉貨啟動，營收動能回升",
            "🔴": "庫存調整未完，需求能見度低",
            "🟡": "手機需求平淡，等待旺季拉貨",
        },
        "伺服器/資料中心": {
            "🟢": "AI伺服器需求爆發，訂單能見度高",
            "🔴": "傳統伺服器需求疲弱",
            "🟡": "AI伺服器獨強，傳統伺服器平淡",
        },
        "網通設備": {
            "🟢": "資料中心升級帶動網通需求",
            "🔴": "企業支出縮減，需求放緩",
            "🟡": "400G/800G升級趨勢持續",
        },
        "PCB/散熱": {
            "🟢": "AI伺服器帶動高階PCB/散熱需求",
            "🔴": "消費性電子需求疲弱",
            "🟡": "AI相關強勁，傳統應用平淡",
        },
        "電源供應": {
            "🟢": "AI伺服器電源需求大增",
            "🔴": "傳統PC/NB需求疲軟",
            "🟡": "高瓦數電源需求成長，低瓦數平淡",
        },
        "面板/顯示": {
            "🟢": "面板報價止跌回升，庫存健康",
            "🔴": "供過於求，面板價格持續下跌",
            "🟡": "大尺寸穩定，中小尺寸競爭激烈",
        },
        "手機供應鏈": {
            "🟢": "新機備貨啟動，供應鏈受惠",
            "🔴": "手機銷售不振，供應鏈承壓",
            "🟡": "旗艦機穩定，中低階競爭激烈",
        },
        "AI應用/平台": {
            "🟢": "企業AI導入加速，應用變現可期",
            "🔴": "AI商業模式待驗證，獲利疑慮",
            "🟡": "AI發展持續，但估值需消化",
        },
        "SaaS/雲服務": {
            "🟢": "企業上雲趨勢延續，訂閱營收成長",
            "🔴": "客戶縮減雲端支出，成長放緩",
            "🟡": "雲端支出優化，聚焦AI功能",
        },
        "科技巨頭": {
            "🟢": "AI投資帶動營收成長，獲利優於預期",
            "🔴": "成長趨緩，AI投資回報受質疑",
            "🟡": "財報分化，AI變現能力成關鍵",
        },
        "AI基礎設施": {
            "🟢": "資本支出持續擴張，基建需求強勁",
            "🔴": "投資放緩疑慮，訂單能見度下降",
            "🟡": "長期需求確定，短期節奏調整",
        },
    }

    # 取得該類別的結論，若無則使用通用模板
    if category in category_conclusions:
        summary = category_conclusions[category].get(light, "本週消息中性，持續觀察")
    else:
        # 通用結論
        if light == "🟢":
            summary = "本週消息正面，產業前景樂觀"
        elif light == "🔴":
            summary = "本週面臨壓力，短期須謹慎"
        else:
            summary = "本週多空交雜，建議觀望"

    return light, summary, trend


def render_category_card(category: str, news_items: list, expanded: bool = False):
    """渲染分類卡片，包含燈號和一句話總結"""
    light, score = analyze_sentiment(news_items)
    summary = generate_summary(category, news_items, light)

    # 標題行：燈號 + 分類 + 數量
    header = f"{light} **{category}** ({len(news_items)} 則)"

    with st.expander(header, expanded=expanded):
        # 一句話總結
        st.markdown(f"**📌 {summary}**")
        st.divider()

        # 新聞列表
        for news in news_items[:5]:
            title = news["title"]
            if len(title) > 80:
                title = title[:80] + "..."
            st.markdown(f"• {title}")
            if news["content"]:
                content_preview = news["content"][:100] + "..." if len(news["content"]) > 100 else news["content"]
                st.caption(f"  {content_preview}")

        if len(news_items) > 5:
            st.caption(f"... 還有 {len(news_items) - 5} 則相關新聞")


def render_summary_page(selected_date: date):
    """渲染總結頁面"""
    st.title("📊 新聞總結")
    st.markdown(f"**日期**: {selected_date.strftime('%Y-%m-%d')}")

    # 取得新聞並套用篩選
    raw_news = get_news_by_date(selected_date)
    ptt_min = st.session_state.get("ptt_min_push", 30)
    exclude_ed = st.session_state.get("exclude_editorial", True)
    news_list = filter_news(raw_news, ptt_min_push=ptt_min, exclude_editorial=exclude_ed)

    # 顯示篩選資訊
    filtered_count = len(raw_news) - len(news_list)
    if filtered_count > 0:
        st.caption(f"🔍 已篩選: 原 {len(raw_news)} 篇 → {len(news_list)} 篇 (過濾 {filtered_count} 篇)")

    stats = get_news_stats_by_date(selected_date)

    if stats["total_count"] == 0:
        st.warning(f"{selected_date} 沒有收集到新聞")
        return

    # 統計卡片
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("新聞總數", stats["total_count"])
    with col2:
        st.metric("RSS 來源", stats["by_source_type"].get("rss", 0))
    with col3:
        st.metric("API 來源", stats["by_source_type"].get("api", 0))
    with col4:
        st.metric("爬蟲來源", stats["by_source_type"].get("scraper", 0))

    st.divider()

    # 分類新聞
    categorized = categorize_news(news_list)

    # ========== 總經趨勢 ==========
    st.header("📈 總經趨勢")

    # 固定分類順序顯示
    macro_news = categorized["macro"]

    # 總覽表格 - 固定順序，分成事實與預期兩欄
    st.markdown("#### 快速總覽")
    overview_data = []
    for category in MACRO_KEYWORDS.keys():
        news_items = macro_news.get(category, [])
        if news_items:
            light, _ = analyze_sentiment(news_items)
            dual = generate_dual_summary(category, news_items)
        else:
            light = "⚪"  # 無資料用灰色
            dual = {"facts": "—", "expectations": "—"}
        overview_data.append({
            "燈號": light,
            "分類": category,
            "📋 確認事實": dual["facts"],
            "🔮 市場預期": dual["expectations"],
            "新聞數": len(news_items)
        })

    df_overview = pd.DataFrame(overview_data)
    # 設定欄位寬度避免破版
    st.dataframe(
        df_overview,
        use_container_width=True,
        hide_index=True,
        column_config={
            "燈號": st.column_config.TextColumn("燈號", width="small"),
            "分類": st.column_config.TextColumn("分類", width="small"),
            "📋 確認事實": st.column_config.TextColumn("📋 確認事實", width="large"),
            "🔮 市場預期": st.column_config.TextColumn("🔮 市場預期", width="medium"),
            "新聞數": st.column_config.NumberColumn("新聞數", width="small"),
        }
    )

    st.markdown("#### 詳細內容")
    for category in MACRO_KEYWORDS.keys():
        news_items = macro_news.get(category, [])
        if news_items:
            render_category_card(category, news_items, expanded=False)
        else:
            with st.expander(f"⚪ **{category}** (0 則)", expanded=False):
                st.caption("今日無相關新聞")

    st.divider()

    # ========== 產業板塊 ==========
    st.header("🏭 產業板塊")
    st.caption("💡 總結基於過去一週新聞趨勢分析，避免單日新聞影響判斷")

    # 取得過去一週新聞用於趨勢分析 (套用篩選)
    raw_weekly = get_weekly_news(selected_date, days=7)
    weekly_news_list = filter_news(raw_weekly, ptt_min_push=ptt_min, exclude_editorial=exclude_ed)
    weekly_categorized = categorize_news(weekly_news_list)
    weekly_industry_news = weekly_categorized["industry"]

    industry_news = categorized["industry"]  # 今日新聞

    # 總覽表格 - 固定順序，使用週趨勢
    st.markdown("#### 快速總覽 (週趨勢)")
    overview_data = []
    for category in INDUSTRY_KEYWORDS.keys():
        daily_items = industry_news.get(category, [])
        weekly_items = weekly_industry_news.get(category, [])

        if weekly_items:
            light, summary, trend = generate_weekly_summary(category, weekly_items, len(daily_items))
        else:
            light = "⚪"
            summary = "本週無相關新聞"
            trend = "—"

        overview_data.append({
            "燈號": light,
            "分類": category,
            "週趨勢": trend,
            "總結": summary,
            "今日": len(daily_items),
            "本週": len(weekly_items)
        })

    df_overview = pd.DataFrame(overview_data)
    st.dataframe(
        df_overview,
        use_container_width=True,
        hide_index=True,
        column_config={
            "燈號": st.column_config.TextColumn("燈號", width="small"),
            "分類": st.column_config.TextColumn("分類", width="small"),
            "週趨勢": st.column_config.TextColumn("週趨勢", width="small"),
            "總結": st.column_config.TextColumn("總結", width="large"),
            "今日": st.column_config.NumberColumn("今日", width="small"),
            "本週": st.column_config.NumberColumn("本週", width="small"),
        }
    )

    st.markdown("#### 詳細內容 (今日新聞)")
    col_left, col_right = st.columns(2)
    for i, category in enumerate(INDUSTRY_KEYWORDS.keys()):
        news_items = industry_news.get(category, [])
        weekly_items = weekly_industry_news.get(category, [])
        with (col_left if i % 2 == 0 else col_right):
            if news_items:
                render_category_card(category, news_items, expanded=False)
            else:
                weekly_count = len(weekly_items)
                with st.expander(f"⚪ **{category}** (今日 0 則 / 週 {weekly_count} 則)", expanded=False):
                    st.caption("今日無相關新聞" if weekly_count == 0 else f"今日無新聞，本週共 {weekly_count} 則")

    st.divider()

    # ========== 科技產業鏈 ==========
    st.header("🔗 科技產業鏈")
    st.caption("💡 總結基於過去一週新聞趨勢分析")

    weekly_tech_news = weekly_categorized["tech_supply_chain"]
    tech_supply_chain_news = categorized["tech_supply_chain"]  # 今日新聞

    # 總覽表格 - 固定順序，使用週趨勢
    st.markdown("#### 快速總覽 (週趨勢)")
    overview_data = []
    for category in TECH_SUPPLY_CHAIN_KEYWORDS.keys():
        daily_items = tech_supply_chain_news.get(category, [])
        weekly_items = weekly_tech_news.get(category, [])

        if weekly_items:
            light, summary, trend = generate_weekly_summary(category, weekly_items, len(daily_items))
        else:
            light = "⚪"
            summary = "本週無相關新聞"
            trend = "—"

        overview_data.append({
            "燈號": light,
            "分類": category,
            "週趨勢": trend,
            "總結": summary,
            "今日": len(daily_items),
            "本週": len(weekly_items)
        })

    df_overview = pd.DataFrame(overview_data)
    st.dataframe(
        df_overview,
        use_container_width=True,
        hide_index=True,
        column_config={
            "燈號": st.column_config.TextColumn("燈號", width="small"),
            "分類": st.column_config.TextColumn("分類", width="small"),
            "週趨勢": st.column_config.TextColumn("週趨勢", width="small"),
            "總結": st.column_config.TextColumn("總結", width="large"),
            "今日": st.column_config.NumberColumn("今日", width="small"),
            "本週": st.column_config.NumberColumn("本週", width="small"),
        }
    )

    st.markdown("#### 詳細內容 (今日新聞)")
    # 使用三欄顯示（因為分類較多）
    col1, col2, col3 = st.columns(3)
    cols = [col1, col2, col3]
    for i, category in enumerate(TECH_SUPPLY_CHAIN_KEYWORDS.keys()):
        daily_items = tech_supply_chain_news.get(category, [])
        weekly_items = weekly_tech_news.get(category, [])
        with cols[i % 3]:
            if daily_items:
                render_category_card(category, daily_items, expanded=False)
            else:
                weekly_count = len(weekly_items)
                with st.expander(f"⚪ **{category}** (今日 0 則 / 週 {weekly_count} 則)", expanded=False):
                    st.caption("今日無相關新聞" if weekly_count == 0 else f"今日無新聞，本週共 {weekly_count} 則")

    st.divider()

    # ========== 數據圖表 ==========
    st.header("📊 數據分析")

    col_left, col_right = st.columns(2)

    with col_left:
        st.subheader("新聞來源分佈")
        if stats["by_source"]:
            df = pd.DataFrame(
                list(stats["by_source"].items()),
                columns=["來源", "數量"]
            )
            st.bar_chart(df.set_index("來源"))

    with col_right:
        st.subheader("熱門關鍵詞")
        all_titles = " ".join([n["title"] for n in news_list])

        keywords = {
            "AI": all_titles.lower().count("ai") + all_titles.lower().count("artificial intelligence"),
            "Fed": all_titles.lower().count("fed"),
            "Trump": all_titles.lower().count("trump"),
            "Gold": all_titles.lower().count("gold"),
            "Tesla": all_titles.lower().count("tesla"),
            "Earnings": all_titles.lower().count("earning"),
            "Tariff": all_titles.lower().count("tariff"),
            "Market": all_titles.lower().count("market"),
            "Economy": all_titles.lower().count("econom"),
            "Rate": all_titles.lower().count("rate"),
        }
        keywords = {k: v for k, v in keywords.items() if v > 0}

        if keywords:
            sorted_kw = sorted(keywords.items(), key=lambda x: x[1], reverse=True)[:8]
            df_kw = pd.DataFrame(sorted_kw, columns=["關鍵詞", "出現次數"])
            st.bar_chart(df_kw.set_index("關鍵詞"))


def render_news_list_page(selected_date: date):
    """渲染新聞列表頁面"""
    st.title("📰 新聞列表")
    st.markdown(f"**日期**: {selected_date.strftime('%Y-%m-%d')}")

    # 取得新聞並套用篩選
    raw_news = get_news_by_date(selected_date)
    ptt_min = st.session_state.get("ptt_min_push", 30)
    exclude_ed = st.session_state.get("exclude_editorial", True)
    news_list = filter_news(raw_news, ptt_min_push=ptt_min, exclude_editorial=exclude_ed)

    # 顯示篩選資訊
    filtered_count = len(raw_news) - len(news_list)
    if filtered_count > 0:
        st.caption(f"🔍 已篩選: 原 {len(raw_news)} 篇 → {len(news_list)} 篇 (過濾 {filtered_count} 篇)")

    if not news_list:
        st.warning(f"{selected_date} 沒有符合篩選條件的新聞")
        return

    col1, col2 = st.columns(2)
    with col1:
        sources = ["全部"] + sorted(list(set(n["source"] for n in news_list if n["source"])))
        selected_source = st.selectbox("來源篩選", sources)
    with col2:
        source_types = ["全部"] + sorted(list(set(n["source_type"] for n in news_list if n["source_type"])))
        selected_type = st.selectbox("類型篩選", source_types)

    search_term = st.text_input("🔍 搜尋標題", "")

    filtered_news = news_list
    if selected_source != "全部":
        filtered_news = [n for n in filtered_news if n["source"] == selected_source]
    if selected_type != "全部":
        filtered_news = [n for n in filtered_news if n["source_type"] == selected_type]
    if search_term:
        filtered_news = [n for n in filtered_news if search_term.lower() in n["title"].lower()]

    st.markdown(f"共 **{len(filtered_news)}** 則新聞")
    st.divider()

    for news in filtered_news:
        with st.expander(f"**{news['title'][:80]}{'...' if len(news['title']) > 80 else ''}**", expanded=False):
            col1, col2 = st.columns([3, 1])
            with col1:
                st.markdown(f"**來源**: {news['source']} ({news['source_type']})")
                if news["published_at"]:
                    st.markdown(f"**發布時間**: {news['published_at']}")
            with col2:
                if news["url"]:
                    st.link_button("🔗 閱讀原文", news["url"])

            if news["content"]:
                st.markdown("**摘要**:")
                st.write(news["content"])


def render_news_detail_page(selected_date: date):
    """渲染新聞詳情頁面"""
    st.title("📄 新聞詳情")
    st.markdown(f"**日期**: {selected_date.strftime('%Y-%m-%d')}")

    news_list = get_news_by_date(selected_date)

    if not news_list:
        st.warning(f"{selected_date} 沒有收集到新聞")
        return

    news_titles = [f"{n['source']}: {n['title'][:60]}..." for n in news_list]
    selected_idx = st.selectbox(
        "選擇新聞",
        range(len(news_titles)),
        format_func=lambda x: news_titles[x]
    )

    if selected_idx is not None:
        news = news_list[selected_idx]

        st.divider()
        st.header(news["title"])

        col1, col2, col3 = st.columns(3)
        with col1:
            st.markdown(f"**來源**: {news['source']}")
        with col2:
            st.markdown(f"**類型**: {news['source_type']}")
        with col3:
            st.markdown(f"**分類**: {news['category']}")

        if news["published_at"]:
            st.markdown(f"**發布時間**: {news['published_at']}")

        st.divider()

        if news["content"]:
            st.subheader("內容摘要")
            st.write(news["content"])
        else:
            st.info("此新聞沒有摘要內容")

        if news["url"]:
            st.divider()
            st.link_button("🔗 點擊閱讀原文", news["url"], use_container_width=True)


def get_ptt_news_by_date(selected_date: date):
    """取得指定日期的 PTT 文章 - 使用統一資料層"""
    try:
        client = _get_data_client()
        # 取得當天新聞並過濾 PTT
        news_list = client.get_news(
            start_date=selected_date,
            end_date=selected_date,
            limit=500
        )
        # 過濾出 PTT 文章
        ptt_news = [n for n in news_list if n.get("source_type") == "ptt"]
        return ptt_news
    except Exception as e:
        return []


def render_ptt_page(selected_date: date):
    """渲染 PTT Stock 頁面"""
    st.title("🇹🇼 PTT Stock 版")
    st.markdown(f"**日期**: {selected_date.strftime('%Y-%m-%d')}")

    raw_ptt = get_ptt_news_by_date(selected_date)

    if not raw_ptt:
        st.warning(f"{selected_date} 沒有 PTT 文章")
        st.info("提示：執行 `python main.py --once` 來收集 PTT 文章")
        return

    # 套用推文數篩選
    ptt_min = st.session_state.get("ptt_min_push", 30)
    ptt_news = filter_news(raw_ptt, ptt_min_push=ptt_min, exclude_editorial=False)

    # 顯示篩選資訊
    filtered_count = len(raw_ptt) - len(ptt_news)
    if filtered_count > 0:
        st.caption(f"🔍 已篩選: 原 {len(raw_ptt)} 篇 → {len(ptt_news)} 篇 (過濾推文數 < {ptt_min} 的 {filtered_count} 篇)")

    if not ptt_news:
        st.warning(f"沒有符合篩選條件的文章 (推文數 >= {ptt_min})")
        return

    # 統計
    categories = {}
    for news in ptt_news:
        cat = news["category"] or "其他"
        categories[cat] = categories.get(cat, 0) + 1

    # 顯示統計
    st.markdown(f"共 **{len(ptt_news)}** 則文章 (推文數 >= {ptt_min})")

    cols = st.columns(len(categories))
    for i, (cat, count) in enumerate(sorted(categories.items(), key=lambda x: x[1], reverse=True)):
        with cols[i % len(cols)]:
            st.metric(cat, count)

    st.divider()

    # 分類篩選
    cat_options = ["全部"] + sorted(categories.keys())
    selected_cat = st.selectbox("分類篩選", cat_options)

    # 搜尋
    search_term = st.text_input("🔍 搜尋標題", "", key="ptt_search")

    # 篩選
    filtered = ptt_news
    if selected_cat != "全部":
        filtered = [n for n in filtered if n["category"] == selected_cat]
    if search_term:
        filtered = [n for n in filtered if search_term.lower() in n["title"].lower()]

    st.markdown(f"顯示 **{len(filtered)}** 則")
    st.divider()

    # 文章列表
    for news in filtered:
        # 取得推文數
        push_info = news["content"] or ""
        push_match = push_info.split("]")[0].replace("[", "") if "]" in push_info else ""

        # 顏色標記推文數
        if "爆" in push_match:
            push_badge = "🔥"
        elif push_match.isdigit() and int(push_match) >= 50:
            push_badge = "🔥"
        elif push_match.startswith("X"):
            push_badge = "💩"
        else:
            push_badge = ""

        # 取得發文時間
        pub_time = ""
        if news["published_at"]:
            try:
                pub_dt = datetime.strptime(news["published_at"], "%Y-%m-%d %H:%M:%S")
                pub_time = pub_dt.strftime("%H:%M")
            except:
                pub_time = ""

        title_display = f"{push_badge} [{news['category']}] {news['title']}"
        if pub_time:
            title_display = f"{pub_time} {title_display}"

        with st.expander(title_display, expanded=False):
            st.markdown(f"**{push_info}**")
            if news["published_at"]:
                st.markdown(f"**發文時間**: {news['published_at']}")

            if news["url"]:
                st.link_button("🔗 前往 PTT 原文", news["url"])


# ========== AI 趨勢雷達系統 ==========
TECH_TRENDS = {
    # ===== AI 運算層 =====
    "GPU/AI晶片": {
        "keywords": ["gpu", "nvidia", "h100", "h200", "b100", "b200", "blackwell", "rubin", "ai chip", "ai accelerator", "grace"],
        "stocks": ["NVDA", "AMD", "INTC", "AVGO", "MRVL"],
        "phase": "成熟期",
        "detail": "Blackwell 量產中，Rubin 2026H2 試產",
    },
    "客製化AI晶片": {
        "keywords": ["custom chip", "asic", "tpu", "trainium", "inferentia", "dojo", "willow"],
        "stocks": ["AVGO", "MRVL", "GOOGL", "AMZN"],
        "phase": "成長期",
        "detail": "雲端大廠自研晶片，Broadcom/Marvell 代工",
    },
    # ===== 記憶體層 =====
    "HBM記憶體": {
        "keywords": ["hbm", "hbm3", "hbm3e", "hbm4", "high bandwidth memory"],
        "stocks": ["MU", "SK Hynix", "Samsung"],
        "phase": "爆發期",
        "detail": "HBM4 2026Q1開始出貨，SK Hynix 市佔70%",
    },
    "DDR5/LPDDR5": {
        "keywords": ["ddr5", "lpddr5", "memory module", "dram"],
        "stocks": ["MU", "SK Hynix", "Samsung"],
        "phase": "成熟期",
        "detail": "伺服器換機潮帶動 DDR5 滲透",
    },
    # ===== 封裝層 =====
    "先進封裝": {
        "keywords": ["cowos", "advanced packaging", "chiplet", "2.5d", "3d packaging", "interposer", "soic", "emib", "foveros"],
        "stocks": ["TSM", "ASX", "AMAT", "INTC"],
        "phase": "爆發期",
        "detail": "CoWoS 2026年底達 13萬片/月",
    },
    # ===== 互連層 =====
    "矽光子/CPO": {
        "keywords": ["silicon photonics", "optical interconnect", "co-packaged optics", "cpo", "photonic", "800g", "1.6t", "3.2t", "odin", "optical engine"],
        "stocks": ["LITE", "COHR", "MRVL", "AVGO", "FN"],
        "phase": "爆發期",
        "detail": "1.6T量產，CPO從實驗轉向必備",
    },
    "高速連接器": {
        "keywords": ["connector", "high speed", "pcie", "ubb", "nvlink", "ethernet switch"],
        "stocks": ["APH", "TEL", "AVGO"],
        "phase": "成長期",
        "detail": "PCIe 6.0/NVLink 5 推動換代",
    },
    # ===== 散熱層 =====
    "液冷散熱": {
        "keywords": ["liquid cooling", "immersion cooling", "direct liquid", "cold plate", "coolant distribution"],
        "stocks": ["VRT", "CARR", "JCI"],
        "phase": "爆發期",
        "detail": "1GW級資料中心標配液冷",
    },
    # ===== 電力層 =====
    "電力基礎設施": {
        "keywords": ["power infrastructure", "data center power", "electricity demand", "grid capacity", "power shortage", "ups", "pdu"],
        "stocks": ["VST", "CEG", "PWR", "ETN", "EMR"],
        "phase": "成長期",
        "detail": "800V HVDC架構普及，電力成瓶頸",
    },
    "核能復興": {
        "keywords": ["nuclear power", "nuclear energy", "smr", "small modular reactor", "uranium", "nuclear renaissance"],
        "stocks": ["CEG", "VST", "CCJ", "NNE", "SMR"],
        "phase": "早期",
        "detail": "微軟/Google/Amazon 簽核電PPA",
    },
    # ===== 雲端/平台層 =====
    "雲端AI服務": {
        "keywords": ["azure ai", "aws", "google cloud", "openai", "anthropic", "cloud ai", "ai infrastructure", "ai spending", "capex"],
        "stocks": ["MSFT", "GOOGL", "AMZN", "ORCL", "META"],
        "phase": "爆發期",
        "detail": "Hyperscaler AI CapEx 持續擴張",
    },
    "AI模型/平台": {
        "keywords": ["chatgpt", "gpt-5", "gemini", "claude", "llama", "openai", "anthropic", "foundation model", "large language model", "llm"],
        "stocks": ["MSFT", "GOOGL", "META", "AMZN"],
        "phase": "成長期",
        "detail": "GPT-5/Gemini 2.0 競爭白熱化",
    },
    "AI資料中心": {
        "keywords": ["ai data center", "hyperscale", "colocation", "data center construction", "ai factory", "gpu cluster"],
        "stocks": ["EQIX", "DLR", "AMT", "MSFT", "GOOGL"],
        "phase": "爆發期",
        "detail": "GW級AI資料中心大量興建",
    },
    # ===== 軟體/應用層 =====
    "AI Agent": {
        "keywords": ["ai agent", "autonomous agent", "agentic ai", "copilot", "mcp", "tool use"],
        "stocks": ["MSFT", "GOOGL", "CRM", "NOW", "PATH"],
        "phase": "成長期",
        "detail": "2026年企業AI Agent大規模部署",
    },
    "企業AI應用": {
        "keywords": ["enterprise ai", "ai saas", "ai software", "ai automation", "workflow ai", "ai analytics"],
        "stocks": ["CRM", "NOW", "WDAY", "SNOW", "PLTR", "PATH"],
        "phase": "成長期",
        "detail": "企業AI軟體訂閱快速成長",
    },
    "邊緣AI": {
        "keywords": ["edge ai", "on-device ai", "npu", "qualcomm ai", "apple intelligence", "ai pc", "ai phone"],
        "stocks": ["QCOM", "AAPL", "ARM", "INTC", "AMD"],
        "phase": "成長期",
        "detail": "AI PC/Phone 換機潮啟動",
    },
    # ===== 設備層 =====
    "半導體設備": {
        "keywords": ["semiconductor equipment", "lithography", "euv", "high na", "etching", "deposition", "inspection"],
        "stocks": ["ASML", "AMAT", "LRCX", "KLAC", "TOELY"],
        "phase": "穩定期",
        "detail": "High-NA EUV 2026量產",
    },
    # ===== 風險 =====
    "地緣政治": {
        "keywords": ["chip ban", "export control", "sanction", "china chip", "huawei", "tariff", "trade war", "entity list"],
        "stocks": [],
        "phase": "風險",
        "detail": "美中科技戰持續，關稅風險",
    },
}

# 關鍵股票詳細對照表
STOCK_DETAILS = {
    # GPU/AI晶片
    "NVDA": {"name": "NVIDIA", "category": "GPU/AI晶片", "role": "AI晶片龍頭，Blackwell/Rubin架構"},
    "AMD": {"name": "AMD", "category": "GPU/AI晶片", "role": "MI300X競爭者，CPU+GPU整合"},
    "INTC": {"name": "Intel", "category": "GPU/AI晶片", "role": "Gaudi加速器，晶圓代工轉型"},
    "AVGO": {"name": "Broadcom", "category": "客製化AI晶片", "role": "客製化AI晶片龍頭，Google TPU設計"},
    "MRVL": {"name": "Marvell", "category": "客製化AI晶片", "role": "雲端客製晶片，收購Celestial AI"},
    # 記憶體
    "MU": {"name": "Micron", "category": "HBM記憶體", "role": "HBM3E供應商，美系唯一"},
    # 封裝
    "TSM": {"name": "TSMC", "category": "先進封裝", "role": "CoWoS/SoIC龍頭，AI封裝市佔80%+"},
    "ASX": {"name": "ASE Technology", "category": "先進封裝", "role": "OSAT龍頭，2.5D/3D封裝"},
    # 矽光子
    "LITE": {"name": "Lumentum", "category": "矽光子/CPO", "role": "雷射/光學元件，CPO關鍵供應商"},
    "COHR": {"name": "Coherent", "category": "矽光子/CPO", "role": "光學模組，800G/1.6T收發器"},
    "FN": {"name": "Fabrinet", "category": "矽光子/CPO", "role": "光學設備代工"},
    # 連接器
    "APH": {"name": "Amphenol", "category": "高速連接器", "role": "高速連接器龍頭，AI伺服器必備"},
    "TEL": {"name": "TE Connectivity", "category": "高速連接器", "role": "連接器/感測器"},
    # 散熱
    "VRT": {"name": "Vertiv", "category": "液冷散熱", "role": "資料中心液冷龍頭"},
    "CARR": {"name": "Carrier Global", "category": "液冷散熱", "role": "HVAC/散熱系統"},
    # 電力
    "VST": {"name": "Vistra", "category": "電力基礎設施", "role": "電力公司，核能資產"},
    "CEG": {"name": "Constellation Energy", "category": "核能復興", "role": "美國最大核電運營商"},
    "PWR": {"name": "Quanta Services", "category": "電力基礎設施", "role": "電力基建工程"},
    "ETN": {"name": "Eaton", "category": "電力基礎設施", "role": "電力管理，UPS/PDU"},
    "CCJ": {"name": "Cameco", "category": "核能復興", "role": "鈾礦龍頭"},
    "SMR": {"name": "NuScale Power", "category": "核能復興", "role": "SMR小型模組核電"},
    # 設備
    "ASML": {"name": "ASML", "category": "半導體設備", "role": "EUV光刻機獨佔"},
    "AMAT": {"name": "Applied Materials", "category": "半導體設備", "role": "沉積/蝕刻設備"},
    "LRCX": {"name": "Lam Research", "category": "半導體設備", "role": "蝕刻設備"},
    "KLAC": {"name": "KLA", "category": "半導體設備", "role": "檢測設備"},
    # 軟體
    "MSFT": {"name": "Microsoft", "category": "AI Agent", "role": "Copilot生態系，Azure AI"},
    "GOOGL": {"name": "Google", "category": "AI Agent", "role": "Gemini，TPU自研"},
    "CRM": {"name": "Salesforce", "category": "AI Agent", "role": "Agentforce企業AI"},
    "NOW": {"name": "ServiceNow", "category": "AI Agent", "role": "企業流程AI自動化"},
    "PATH": {"name": "UiPath", "category": "企業AI應用", "role": "RPA/流程自動化龍頭"},
    # 邊緣
    "QCOM": {"name": "Qualcomm", "category": "邊緣AI", "role": "手機/PC NPU龍頭"},
    "AAPL": {"name": "Apple", "category": "邊緣AI", "role": "Apple Intelligence生態"},
    "ARM": {"name": "ARM Holdings", "category": "邊緣AI", "role": "CPU架構授權"},
    # 雲端/平台
    "AMZN": {"name": "Amazon", "category": "雲端AI服務", "role": "AWS雲端龍頭，Bedrock AI平台"},
    "ORCL": {"name": "Oracle", "category": "雲端AI服務", "role": "OCI雲端，企業AI資料庫"},
    "META": {"name": "Meta", "category": "AI模型/平台", "role": "Llama開源模型，AI廣告應用"},
    # 資料中心
    "EQIX": {"name": "Equinix", "category": "AI資料中心", "role": "全球最大資料中心REIT"},
    "DLR": {"name": "Digital Realty", "category": "AI資料中心", "role": "資料中心REIT，Hyperscaler客戶"},
    "AMT": {"name": "American Tower", "category": "AI資料中心", "role": "通訊塔/邊緣資料中心"},
    # 企業軟體
    "WDAY": {"name": "Workday", "category": "企業AI應用", "role": "HR/財務SaaS，AI助理"},
    "SNOW": {"name": "Snowflake", "category": "企業AI應用", "role": "雲端資料倉儲，AI/ML平台"},
    "PLTR": {"name": "Palantir", "category": "企業AI應用", "role": "AI數據分析平台，政府/企業"},
    # ETF (用於2022防禦配置)
    "XLE": {"name": "Energy Select ETF", "category": "ETF", "role": "能源板塊ETF"},
    "XLF": {"name": "Financial Select ETF", "category": "ETF", "role": "金融板塊ETF"},
    "XLV": {"name": "Health Care Select ETF", "category": "ETF", "role": "醫療板塊ETF"},
    "XLU": {"name": "Utilities Select ETF", "category": "ETF", "role": "公用事業ETF"},
    "SHY": {"name": "iShares 1-3Y Treasury", "category": "ETF", "role": "短期國債ETF"},
    # 防禦股
    "JPM": {"name": "JPMorgan Chase", "category": "金融", "role": "美國最大銀行"},
    "JNJ": {"name": "Johnson & Johnson", "category": "醫療", "role": "醫療保健龍頭"},
    "PG": {"name": "Procter & Gamble", "category": "必需消費", "role": "消費品龍頭"},
    "COST": {"name": "Costco", "category": "必需消費", "role": "會員制零售"},
}

# 2026 Q1 技術預測
Q1_2026_FORECAST = {
    "GPU/AI晶片": {
        "status": "🟢 量產",
        "milestone": "Blackwell B200 全面量產，Rubin R100 進入試產",
        "bottleneck": "CoWoS封裝產能仍緊",
        "catalyst": "NVIDIA GTC 2026 (3月)",
    },
    "HBM記憶體": {
        "status": "🔥 爆發",
        "milestone": "HBM4 開始出貨，頻寬達 2TB/s",
        "bottleneck": "HBM4 良率爬坡中",
        "catalyst": "SK Hynix HBM4 量產宣布",
    },
    "先進封裝": {
        "status": "🔥 爆發",
        "milestone": "CoWoS月產能達10萬片，CoWoS-L量產",
        "bottleneck": "ABF載板供應",
        "catalyst": "TSMC法說會 (1月)",
    },
    "矽光子/CPO": {
        "status": "🚀 轉折點",
        "milestone": "1.6T模組量產，CPO從實驗轉必備",
        "bottleneck": "InP雷射供應",
        "catalyst": "OFC 2026 (3月)",
    },
    "液冷散熱": {
        "status": "🟢 成長",
        "milestone": "液冷滲透率達40%+",
        "bottleneck": "客製化設計週期",
        "catalyst": "新資料中心標案",
    },
    "電力基礎設施": {
        "status": "⚠️ 瓶頸",
        "milestone": "800V HVDC成新標準",
        "bottleneck": "電網容量不足",
        "catalyst": "核電PPA簽約消息",
    },
    "AI Agent": {
        "status": "🌱 早期",
        "milestone": "企業Agent大規模POC",
        "bottleneck": "可靠性/安全性",
        "catalyst": "微軟/Salesforce產品發布",
    },
    "雲端AI服務": {
        "status": "🔥 爆發",
        "milestone": "AI CapEx 達GDP佔比新高",
        "bottleneck": "GPU供應/電力取得",
        "catalyst": "Hyperscaler財報 (CapEx指引)",
    },
    "AI模型/平台": {
        "status": "🟢 成長",
        "milestone": "GPT-5/Gemini 2.0 發布，多模態標配",
        "bottleneck": "訓練成本/算力需求",
        "catalyst": "OpenAI/Google新模型發布",
    },
    "AI資料中心": {
        "status": "🔥 爆發",
        "milestone": "GW級AI園區動工，液冷標配",
        "bottleneck": "電力/土地/許可證",
        "catalyst": "新資料中心動工消息",
    },
    "企業AI應用": {
        "status": "🟢 成長",
        "milestone": "AI SaaS滲透率達15%+",
        "bottleneck": "企業資料準備度",
        "catalyst": "企業軟體財報 (AI營收佔比)",
    },
}

SUPPLY_CHAIN_KEYWORDS = {
    "短缺警示": ["shortage", "constraint", "bottleneck", "tight supply", "allocation", "lead time extend"],
    "產能動態": ["capacity expansion", "new fab", "foundry", "utilization", "ramp up", "mass production"],
    "價格變動": ["price hike", "price increase", "price cut", "asp", "margin pressure"],
    "需求信號": ["strong demand", "order", "backlog", "booking", "guidance raise", "beat estimate"],
}


def analyze_trend_from_news(news_list: list) -> dict:
    """分析新聞中的技術趨勢"""
    from collections import defaultdict

    daily_mentions = defaultdict(lambda: defaultdict(int))
    total_mentions = defaultdict(int)

    for news in news_list:
        title = (news.get("title") or "").lower()
        content = (news.get("content") or "").lower()
        text = title + " " + content

        pub_date = news.get("published_at") or news.get("collected_at") or ""
        if pub_date:
            date_str = pub_date[:10]
        else:
            continue

        for trend_name, trend_info in TECH_TRENDS.items():
            for keyword in trend_info["keywords"]:
                if keyword.lower() in text:
                    daily_mentions[date_str][trend_name] += 1
                    total_mentions[trend_name] += 1
                    break

    # 計算動能
    today = date.today()
    recent_7d = set((today - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(7))
    prev_7d = set((today - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(7, 14))

    momentum = {}
    for trend_name in TECH_TRENDS.keys():
        recent = sum(daily_mentions[d][trend_name] for d in recent_7d)
        prev = sum(daily_mentions[d][trend_name] for d in prev_7d)
        change_pct = ((recent - prev) / prev * 100) if prev > 0 else (100 if recent > 0 else 0)
        momentum[trend_name] = {"recent": recent, "prev": prev, "change_pct": change_pct, "total": total_mentions[trend_name]}

    return {"daily_mentions": dict(daily_mentions), "momentum": momentum}


def detect_supply_chain_alerts(news_list: list) -> list:
    """偵測供應鏈警示"""
    alerts = []
    seen_titles = set()

    for news in news_list:
        title = news.get("title") or ""
        if title in seen_titles:
            continue

        text = (title + " " + (news.get("content") or "")).lower()

        for alert_type, keywords in SUPPLY_CHAIN_KEYWORDS.items():
            for kw in keywords:
                if kw in text:
                    related = [t for t, info in TECH_TRENDS.items() if any(k in text for k in info["keywords"])]
                    if related:
                        seen_titles.add(title)
                        alerts.append({
                            "type": alert_type,
                            "title": title,
                            "date": (news.get("published_at") or "")[:10],
                            "related": related,
                            "url": news.get("url"),
                        })
                        break
                break

    return alerts[:30]


def render_trend_radar_page():
    """渲染趨勢雷達頁面"""
    st.title("🎯 AI 趨勢雷達")
    st.markdown("**追蹤 AI 產業鏈技術演進、供應鏈瓶頸與投資輪動**")

    # 時間範圍選擇
    col1, col2 = st.columns([1, 3])
    with col1:
        time_range = st.selectbox(
            "時間範圍",
            ["1個月", "3個月", "6個月"],
            index=2  # 預設6個月
        )

    days_map = {"1個月": 30, "3個月": 90, "6個月": 180}
    days = days_map[time_range]

    end_date = date.today()
    start_date = end_date - timedelta(days=days)

    # 取得新聞 - 使用統一資料層
    @st.cache_data(ttl=1800)
    def get_trend_news(start_str: str):
        try:
            client = _get_data_client()
            start_dt = datetime.strptime(start_str, "%Y-%m-%d").date()
            end_dt = date.today()
            news_list = client.get_news(
                start_date=start_dt,
                end_date=end_dt,
                limit=10000
            )
            return news_list
        except Exception as e:
            return []

    raw_news = get_trend_news(start_date.isoformat())
    if not raw_news:
        st.warning("沒有足夠的新聞數據")
        return

    # 套用篩選
    ptt_min = st.session_state.get("ptt_min_push", 30)
    exclude_ed = st.session_state.get("exclude_editorial", True)
    news_list = filter_news(raw_news, ptt_min_push=ptt_min, exclude_editorial=exclude_ed)

    filtered_count = len(raw_news) - len(news_list)
    filter_info = f" (已過濾 {filtered_count} 篇)" if filtered_count > 0 else ""
    st.caption(f"📰 分析 {len(news_list)} 篇新聞 ({start_date} ~ {end_date}){filter_info}")

    trend_data = analyze_trend_from_news(news_list)
    momentum = trend_data["momentum"]

    # ========== 熱度排行 ==========
    st.header("🔥 趨勢熱度排行 (週變化)")

    sorted_trends = sorted(momentum.items(), key=lambda x: x[1]["change_pct"], reverse=True)

    cols = st.columns(4)
    for i, (name, data) in enumerate(sorted_trends[:8]):
        with cols[i % 4]:
            change = data["change_pct"]
            emoji = "🚀" if change > 50 else ("📈" if change > 0 else ("➡️" if change > -20 else "📉"))
            phase = TECH_TRENDS[name]["phase"]
            stocks = ", ".join(TECH_TRENDS[name]["stocks"][:2]) or "—"

            st.metric(
                label=f"{emoji} {name}",
                value=f"{data['recent']} 則",
                delta=f"{change:+.0f}% vs 上週",
                help=f"階段: {phase} | 股票: {stocks}"
            )

    st.divider()

    # ========== 趨勢時間線 ==========
    st.header("📊 趨勢時間線")

    selected = st.multiselect(
        "選擇主題", list(TECH_TRENDS.keys()),
        default=["GPU/AI晶片", "HBM記憶體", "矽光子/CPO", "電力基礎設施"]
    )

    if selected:
        daily = trend_data["daily_mentions"]
        dates = sorted(daily.keys())[-days:]  # 使用選擇的時間範圍

        # 根據時間範圍調整移動平均窗口
        window = 7 if days >= 90 else 3

        fig = go.Figure()
        for trend in selected:
            vals = [daily.get(d, {}).get(trend, 0) for d in dates]
            smoothed = pd.Series(vals).rolling(window, min_periods=1).mean()
            fig.add_trace(go.Scatter(x=dates, y=smoothed, mode='lines', name=trend, line=dict(width=2)))

        fig.update_layout(
            height=500,
            xaxis_title="日期",
            yaxis_title=f"新聞提及數 ({window}日均線)",
            legend=dict(orientation="h", y=1.1),
            hovermode="x unified"
        )
        st.plotly_chart(fig, use_container_width=True)

        # 趨勢摘要
        st.markdown("#### 📈 趨勢變化摘要")
        # 計算各階段變化
        if len(dates) >= 60:
            mid_point = len(dates) // 2
            first_half = dates[:mid_point]
            second_half = dates[mid_point:]

            summary_data = []
            for trend in selected:
                first_count = sum(daily.get(d, {}).get(trend, 0) for d in first_half)
                second_count = sum(daily.get(d, {}).get(trend, 0) for d in second_half)
                if first_count > 0:
                    change = ((second_count - first_count) / first_count) * 100
                else:
                    change = 100 if second_count > 0 else 0

                trend_direction = "📈 上升" if change > 20 else ("📉 下降" if change < -20 else "➡️ 持平")
                summary_data.append({
                    "主題": trend,
                    "前半期": first_count,
                    "後半期": second_count,
                    "變化": f"{change:+.0f}%",
                    "趨勢": trend_direction,
                })

            st.dataframe(pd.DataFrame(summary_data), use_container_width=True, hide_index=True)

    st.divider()

    # ========== 供應鏈警示 ==========
    st.header("⚠️ 供應鏈警示")

    alerts = detect_supply_chain_alerts(news_list)
    if alerts:
        alert_types = list(set(a["type"] for a in alerts))
        tabs = st.tabs(alert_types)

        for tab, atype in zip(tabs, alert_types):
            with tab:
                for a in [x for x in alerts if x["type"] == atype][:8]:
                    st.markdown(f"**{a['date']}** | {', '.join(a['related'])}")
                    if a.get("url"):
                        st.markdown(f"[{a['title']}]({a['url']})")
                    else:
                        st.markdown(a['title'])
                    st.markdown("---")
    else:
        st.info("暫無重大供應鏈警示")

    st.divider()

    # ========== 投資地圖 ==========
    st.header("📋 AI 產業鏈投資地圖")

    table_data = []
    for name, info in TECH_TRENDS.items():
        m = momentum.get(name, {})
        table_data.append({
            "主題": name,
            "階段": info["phase"],
            "近7天": m.get("recent", 0),
            "週變化": f"{m.get('change_pct', 0):+.0f}%",
            "相關股票": ", ".join(info["stocks"][:3]) or "—",
        })

    st.dataframe(pd.DataFrame(table_data), use_container_width=True, hide_index=True)

    with st.expander("📖 投資階段說明"):
        st.markdown("""
        | 階段 | 特徵 | 策略建議 |
        |------|------|----------|
        | 🌱 **早期** | 技術萌芽，提及數少但在上升 | 小部位佈局，高風險高報酬 |
        | 📈 **成長期** | 技術驗證，需求快速增加 | 積極加碼，追蹤龍頭 |
        | 🔥 **爆發期** | 供不應求，股價飆漲 | 核心持股，留意過熱 |
        | 📊 **成熟期** | 技術普及，競爭加劇 | 選龍頭，留意毛利 |
        | ⏸️ **穩定期** | 需求穩定，成長放緩 | 價值投資，領息 |
        | ⚠️ **風險** | 地緣政治/監管風險 | 避險或觀望 |
        """)

    # ========== 2026 Q1 技術預測 ==========
    st.header("🔮 2026 Q1 技術預測")

    forecast_data = []
    for tech, info in Q1_2026_FORECAST.items():
        forecast_data.append({
            "技術領域": tech,
            "狀態": info["status"],
            "里程碑": info["milestone"],
            "瓶頸": info["bottleneck"],
            "催化劑": info["catalyst"],
        })

    st.dataframe(pd.DataFrame(forecast_data), use_container_width=True, hide_index=True)

    st.divider()

    # ========== 關鍵股票對照表 ==========
    st.header("📈 關鍵股票對照表")

    # 按類別分組
    categories = {}
    for symbol, info in STOCK_DETAILS.items():
        cat = info["category"]
        if cat not in categories:
            categories[cat] = []
        categories[cat].append({"代碼": symbol, "公司": info["name"], "角色": info["role"]})

    # 選擇類別
    selected_cat = st.selectbox("選擇技術領域", list(categories.keys()))

    if selected_cat:
        st.dataframe(pd.DataFrame(categories[selected_cat]), use_container_width=True, hide_index=True)

        # 顯示相關趨勢
        trend_info = TECH_TRENDS.get(selected_cat, {})
        if trend_info:
            col1, col2 = st.columns(2)
            with col1:
                st.metric("階段", trend_info.get("phase", "—"))
            with col2:
                st.metric("關鍵字", ", ".join(trend_info.get("keywords", [])[:5]))
            if trend_info.get("detail"):
                st.info(f"📌 {trend_info['detail']}")

    st.divider()

    # ========== 技術演進路線圖 ==========
    with st.expander("🗺️ AI 技術演進路線圖 (2024-2027) - 含回測股票"):
        st.markdown("""
        ### 技術演進與對應股票

        | 領域 | 2024 H1 | 2024 H2 | 2025 H1 | 2025 H2 | 2026 Q1 | 2026 H2 | 2027+ |
        |------|---------|---------|---------|---------|---------|---------|-------|
        | **GPU運算** | H100 | H200 | B100/B200(試產) | Blackwell(量產) | Blackwell(放量) | Rubin R100(試產) | R200 |
        | 股票 | NVDA | NVDA, AMD | NVDA, AMD, AVGO | NVDA, AMD, AVGO | NVDA, AMD, MRVL | NVDA, AMD | NVDA |
        | **記憶體** | HBM3 | HBM3E | HBM3E(供不應求) | HBM3E+HBM4試產 | HBM4量產 | HBM4E | HBM4+ |
        | 股票 | MU | MU | MU | MU | MU | MU | MU |
        | **先進封裝** | CoWoS-S(5萬片) | CoWoS-S(6萬片) | CoWoS-L(7.5萬片) | CoWoS-L(量產) | CoWoS-R(10萬片) | SoIC(13萬片) | 3D IC |
        | 股票 | TSM, ASX | TSM, ASX | TSM, ASX | TSM, ASX | TSM, ASX | TSM, ASX | TSM |
        | **光互連** | 400G | 800G(量產) | 800G(主流) | 1.6T(試產) | 1.6T量產+CPO | 3.2T | CPO標配 |
        | 股票 | COHR | COHR, LITE | LITE, COHR, FN | LITE, COHR, MRVL | LITE, COHR, MRVL | LITE, MRVL | MRVL |
        | **散熱** | 氣冷 | 氣冷+液冷 | 液冷(成長) | 液冷(爆發) | 液冷40%+ | 浸沒式 | 液冷標配 |
        | 股票 | - | VRT | VRT, CARR | VRT, CARR | VRT | VRT | VRT |
        | **電力** | 傳統電網 | 電力緊張 | 核電PPA | 800V HVDC | 電網瓶頸 | SMR佈局 | 核能+再生 |
        | 股票 | - | VST, ETN | CEG, CCJ, VST | CEG, CCJ, ETN, PWR | CEG, CCJ, SMR | SMR, CEG | SMR |
        | **雲端AI** | ChatGPT爆發 | AI CapEx啟動 | CapEx加速 | CapEx高峰 | CapEx持續 | 效率優化 | 下一波 |
        | 股票 | MSFT, GOOGL | MSFT, AMZN, GOOGL | MSFT, AMZN, GOOGL, META | MSFT, AMZN, ORCL | MSFT, AMZN, ORCL | MSFT, GOOGL | - |
        | **資料中心** | 傳統DC | AI DC規劃 | AI DC動工 | GW級園區 | 擴產加速 | 邊緣DC | 分散式 |
        | 股票 | EQIX, DLR | EQIX, DLR | EQIX, DLR, AMT | EQIX, DLR | EQIX, DLR | AMT | - |
        | **企業AI** | POC階段 | 試點部署 | 規模部署 | 營收貢獻 | AI佔比15%+ | 標配 | AI原生 |
        | 股票 | - | CRM, NOW | CRM, NOW, PLTR | CRM, NOW, PLTR, WDAY | CRM, NOW, SNOW, PATH | 全部 | - |

        ---

        ### 📊 回測建議：各時期核心持股

        | 時期 | 主題焦點 | 核心持股 | 輔助持股 |
        |------|----------|----------|----------|
        | **2024 H1** | GPU需求爆發 | NVDA | MSFT, GOOGL, TSM |
        | **2024 H2** | 記憶體/封裝緊張 | NVDA, MU, TSM | COHR, VRT |
        | **2025 H1** | 光互連崛起 | NVDA, LITE, TSM | MU, VRT, CEG |
        | **2025 H2** | HBM4+CPO轉折 | MU, LITE, MRVL | NVDA, TSM, CEG |
        | **2026 Q1** | 多元爆發 | LITE, MU, CEG | NVDA, VRT, CRM |
        | **2026 H2** | 次世代佈局 | SMR, MRVL, NVDA | VRT, TSM, NOW |
        """)

    # ========== 投資主題摘要 ==========
    with st.expander("💡 2026 Q1 投資主題摘要"):
        st.markdown("""
        ### 🔥 最熱門主題 (爆發期)

        | 主題 | 核心邏輯 | 首選股票 |
        |------|----------|----------|
        | **HBM記憶體** | HBM4出貨啟動，SK Hynix市佔70% | MU, SK Hynix |
        | **矽光子/CPO** | 1.6T量產，CPO從實驗變必備 | LITE, COHR, MRVL |
        | **先進封裝** | CoWoS月產能翻倍，仍供不應求 | TSM, ASX |
        | **液冷散熱** | 1GW級資料中心標配液冷 | VRT |

        ### 🌱 早期佈局 (高風險高報酬)

        | 主題 | 核心邏輯 | 首選股票 |
        |------|----------|----------|
        | **核能復興** | 科技巨頭簽核電PPA | CEG, CCJ, SMR |
        | **AI Agent** | 企業Agent大規模部署元年 | CRM, NOW, MSFT |

        ### ⚠️ 風險關注

        | 風險 | 影響 | 應對 |
        |------|------|------|
        | **電力瓶頸** | 資料中心選址受限，電價上漲 | 關注電力股 VST, PWR |
        | **關稅戰** | 半導體設備/零件成本上升 | 避免高中國曝險股 |
        | **估值過高** | AI股整體估值偏高 | 分批佈局，留意回調 |
        """)


# ========== 季度持股池回測系統 ==========
# 基於「季初可得資訊」的信號系統，避免後見之明

# 季初信號定義（這些是每季開始時就能觀察到的）
QUARTER_SIGNALS = {
    "2022Q1": {
        "fed_stance": "即將升息",      # 2021/12 Fed點陣圖顯示2022升息
        "yield_curve": "正常但趨平",    # 10Y-2Y 約 0.8%
        "cpi_trend": "上升 (7%)",       # 2021/12 CPI 7.0%
        "spy_vs_200ma": "上方",         # SPY 在 200MA 上方
        "vix": "中等 (17)",
        "signal_score": 0.3,            # -1(極度防禦) 到 1(極度積極)
    },
    "2022Q2": {
        "fed_stance": "激進升息中",     # 3月升息1碼，暗示加速
        "yield_curve": "趨平",          # 10Y-2Y 接近 0
        "cpi_trend": "加速 (8.5%)",     # 2022/03 CPI 8.5%
        "spy_vs_200ma": "跌破",         # SPY 跌破 200MA
        "vix": "偏高 (21)",
        "signal_score": -0.3,
    },
    "2022Q3": {
        "fed_stance": "持續鷹派",       # 6月升息3碼
        "yield_curve": "倒掛",          # 10Y-2Y 轉負
        "cpi_trend": "高峰 (9.1%)",     # 2022/06 CPI 9.1%
        "spy_vs_200ma": "下方",
        "vix": "偏高 (26)",
        "signal_score": -0.5,
    },
    "2022Q4": {
        "fed_stance": "鷹派但放緩",     # 持續升息但幅度可能減
        "yield_curve": "倒掛",
        "cpi_trend": "開始下滑 (8.2%)", # 2022/09 CPI 8.2%
        "spy_vs_200ma": "下方",
        "vix": "高 (31)",
        "signal_score": -0.2,
    },
    "2023Q1": {
        "fed_stance": "升息尾聲",       # 市場預期接近終點
        "yield_curve": "深度倒掛",
        "cpi_trend": "下滑 (6.5%)",
        "spy_vs_200ma": "接近",
        "vix": "下降 (21)",
        "signal_score": 0.2,
    },
    "2023Q2": {
        "fed_stance": "接近暫停",
        "yield_curve": "倒掛",
        "cpi_trend": "持續下滑 (5%)",
        "spy_vs_200ma": "上方",         # 突破 200MA
        "vix": "低 (17)",
        "ai_momentum": "ChatGPT用戶破億", # 新信號：AI題材
        "signal_score": 0.5,
    },
    "2023Q3": {
        "fed_stance": "暫停觀望",
        "yield_curve": "倒掛",
        "cpi_trend": "下滑 (3.2%)",
        "spy_vs_200ma": "上方",
        "vix": "低 (14)",
        "ai_momentum": "NVDA財報超預期",
        "signal_score": 0.6,
    },
    "2023Q4": {
        "fed_stance": "暫停，降息預期",
        "yield_curve": "倒掛收窄",
        "cpi_trend": "穩定 (3.7%)",
        "spy_vs_200ma": "上方",
        "vix": "低 (17)",
        "ai_momentum": "AI CapEx確認增加",
        "signal_score": 0.7,
    },
    "2024Q1": {
        "fed_stance": "維持，等待降息",
        "yield_curve": "倒掛收窄",
        "cpi_trend": "穩定 (3.4%)",
        "spy_vs_200ma": "上方",
        "vix": "低 (13)",
        "ai_momentum": "Hyperscaler CapEx指引強勁",
        "signal_score": 0.7,
    },
    "2024Q2": {
        "fed_stance": "維持觀望",
        "yield_curve": "倒掛",
        "cpi_trend": "略升 (3.5%)",
        "spy_vs_200ma": "上方",
        "vix": "低 (13)",
        "ai_momentum": "HBM供不應求",
        "signal_score": 0.6,
    },
    "2024Q3": {
        "fed_stance": "即將降息",
        "yield_curve": "倒掛收窄",
        "cpi_trend": "下滑 (2.9%)",
        "spy_vs_200ma": "上方",
        "vix": "中等 (15)",
        "ai_momentum": "800G量產，光互連題材",
        "signal_score": 0.6,
    },
    "2024Q4": {
        "fed_stance": "降息開始",
        "yield_curve": "正常化",
        "cpi_trend": "穩定 (2.6%)",
        "spy_vs_200ma": "上方",
        "vix": "中等 (16)",
        "ai_momentum": "核電PPA簽約，電力瓶頸",
        "signal_score": 0.5,
    },
    "2025Q1": {
        "fed_stance": "降息暫停",       # 1月Fed維持利率
        "yield_curve": "正常",
        "cpi_trend": "略升 (2.9%)",
        "spy_vs_200ma": "跌破後反彈",   # 1月底跌破，2月反彈
        "vix": "飆升 (16→28)",          # DeepSeek後VIX飆升至28
        "ai_momentum": "DeepSeek衝擊，AI估值重估",
        "tariff_risk": "川普關稅威脅升級",
        "signal_score": -0.3,           # 熊市信號！
    },
    # ===== 以下為未來預測 (假設情境) =====
    "2025Q2": {
        "fed_stance": "觀望",
        "yield_curve": "正常",
        "cpi_trend": "待觀察",
        "spy_vs_200ma": "待觀察",
        "vix": "待觀察 (關稅談判)",
        "ai_momentum": "關稅影響待釐清",
        "tariff_risk": "關稅談判進行中",
        "signal_score": -0.1,           # 仍偏保守
    },
    "2025Q3": {
        "fed_stance": "可能降息",
        "yield_curve": "正常",
        "cpi_trend": "穩定",
        "spy_vs_200ma": "待觀察",
        "vix": "待觀察",
        "ai_momentum": "HBM4量產",
        "signal_score": 0.5,
    },
    "2025Q4": {
        "fed_stance": "寬鬆週期",
        "yield_curve": "正常",
        "cpi_trend": "穩定",
        "spy_vs_200ma": "待觀察",
        "vix": "待觀察",
        "ai_momentum": "AI全面滲透",
        "signal_score": 0.5,
    },
    "2026Q1": {
        "fed_stance": "寬鬆",
        "yield_curve": "正常",
        "cpi_trend": "穩定",
        "spy_vs_200ma": "待觀察",
        "vix": "待觀察",
        "ai_momentum": "Rubin預熱",
        "signal_score": 0.5,
    },
}

def get_allocation_from_signal(signal_score: float, ai_momentum: bool = False) -> dict:
    """根據信號分數決定配置風格

    signal_score: -1 (極度防禦) 到 1 (極度積極)
    """
    if signal_score <= -0.5:
        # 極度防禦
        return {"style": "極度防禦", "equity": 0.40, "defensive": 0.40, "bond": 0.20}
    elif signal_score <= -0.2:
        # 防禦
        return {"style": "防禦", "equity": 0.55, "defensive": 0.30, "bond": 0.15}
    elif signal_score <= 0.2:
        # 中性
        return {"style": "中性", "equity": 0.70, "defensive": 0.20, "bond": 0.10}
    elif signal_score <= 0.5:
        # 積極
        return {"style": "積極", "equity": 0.85, "defensive": 0.10, "bond": 0.05}
    else:
        # 極度積極
        return {"style": "極度積極", "equity": 0.95, "defensive": 0.05, "bond": 0.00}


# 基於季初信號的持股配置
QUARTERLY_PORTFOLIOS = {
    # ===== 2022 年 =====
    "2022Q1": {
        "name": "升息預期啟動",
        "signal": "Fed點陣圖顯示升息，CPI 7%，但SPY仍在200MA上",
        "start": "2022-01-01",
        "end": "2022-03-31",
        "holdings": {
            # signal_score: 0.3 (中性偏積極)
            "XLE": 0.20,    # 通膨受惠
            "XLF": 0.15,    # 升息受惠
            "MSFT": 0.15,
            "AAPL": 0.15,
            "GOOGL": 0.12,
            "XLV": 0.12,    # 部分防禦
            "JPM": 0.11,
        },
    },
    "2022Q2": {
        "name": "SPY跌破200MA",
        "signal": "SPY跌破200MA，CPI加速至8.5%，Fed升息加速",
        "start": "2022-04-01",
        "end": "2022-06-30",
        "holdings": {
            # signal_score: -0.3 (防禦)
            "XLE": 0.25,    # 能源通膨受惠
            "XLV": 0.20,    # 防禦
            "XLU": 0.15,    # 防禦
            "XLF": 0.15,    # 升息受惠
            "COST": 0.13,   # 必需消費
            "JNJ": 0.12,
        },
    },
    "2022Q3": {
        "name": "殖利率倒掛確認",
        "signal": "10Y-2Y倒掛，CPI達9.1%高峰，VIX 26",
        "start": "2022-07-01",
        "end": "2022-09-30",
        "holdings": {
            # signal_score: -0.5 (極度防禦)
            "XLE": 0.20,
            "XLV": 0.20,
            "XLU": 0.20,
            "SHY": 0.15,    # 短債避險
            "COST": 0.13,
            "PG": 0.12,
        },
    },
    "2022Q4": {
        "name": "CPI見頂信號",
        "signal": "CPI從9.1%降至8.2%，通膨可能見頂",
        "start": "2022-10-01",
        "end": "2022-12-31",
        "holdings": {
            # signal_score: -0.2 (防禦但開始試探)
            "XLE": 0.18,
            "XLV": 0.15,
            "XLF": 0.12,
            "MSFT": 0.12,
            "AAPL": 0.12,
            "GOOGL": 0.10,
            "XLU": 0.10,
            "AMZN": 0.11,
        },
    },
    # ===== 2023 年 =====
    "2023Q1": {
        "name": "升息尾聲預期",
        "signal": "CPI降至6.5%，市場預期Fed接近終點",
        "start": "2023-01-01",
        "end": "2023-03-31",
        "holdings": {
            # signal_score: 0.2 (中性)
            "MSFT": 0.18,   # ChatGPT題材 (2022/11上線)
            "NVDA": 0.15,   # GPU需求預期
            "GOOGL": 0.15,
            "META": 0.12,   # 效率年題材
            "AAPL": 0.12,
            "XLV": 0.15,    # 維持防禦
            "AMZN": 0.13,
        },
    },
    "2023Q2": {
        "name": "AI需求確認",
        "signal": "SPY突破200MA，ChatGPT用戶破億，NVDA指引超預期",
        "start": "2023-04-01",
        "end": "2023-06-30",
        "holdings": {
            # signal_score: 0.5 (積極)
            "NVDA": 0.28,
            "MSFT": 0.18,
            "AMD": 0.12,
            "GOOGL": 0.12,
            "META": 0.10,
            "TSM": 0.10,
            "XLV": 0.10,    # 少量防禦
        },
    },
    "2023Q3": {
        "name": "AI CapEx確認",
        "signal": "Hyperscaler財報確認AI投資，VIX 14低檔",
        "start": "2023-07-01",
        "end": "2023-09-30",
        "holdings": {
            # signal_score: 0.6 (積極)
            "NVDA": 0.28,
            "MSFT": 0.18,
            "AMD": 0.10,
            "AVGO": 0.10,
            "TSM": 0.10,
            "GOOGL": 0.10,
            "AMZN": 0.09,
            "XLV": 0.05,
        },
    },
    "2023Q4": {
        "name": "降息預期升溫",
        "signal": "Fed暫停升息，市場開始定價2024降息",
        "start": "2023-10-01",
        "end": "2023-12-31",
        "holdings": {
            # signal_score: 0.7 (極度積極)
            "NVDA": 0.25,
            "MSFT": 0.18,
            "AMD": 0.10,
            "AVGO": 0.10,
            "META": 0.10,
            "GOOGL": 0.10,
            "TSM": 0.10,
            "AMZN": 0.07,
        },
    },
    # ===== 2024 年 =====
    "2024Q1": {
        "name": "AI CapEx指引強勁",
        "signal": "Hyperscaler 2024 CapEx指引大增，VIX低檔",
        "start": "2024-01-01",
        "end": "2024-03-31",
        "holdings": {
            # signal_score: 0.7
            "NVDA": 0.30,
            "MSFT": 0.18,
            "TSM": 0.12,
            "GOOGL": 0.12,
            "AVGO": 0.12,
            "AMD": 0.08,
            "META": 0.08,
        },
    },
    "2024Q2": {
        "name": "HBM供需緊張",
        "signal": "HBM供不應求新聞增加，記憶體股受關注",
        "start": "2024-04-01",
        "end": "2024-06-30",
        "holdings": {
            # signal_score: 0.6
            "NVDA": 0.25,
            "MU": 0.15,
            "TSM": 0.15,
            "MSFT": 0.12,
            "AVGO": 0.12,
            "AMD": 0.08,
            "GOOGL": 0.08,
            "XLV": 0.05,
        },
    },
    "2024Q3": {
        "name": "光互連題材",
        "signal": "800G量產新聞增加，矽光子/CPO題材浮現",
        "start": "2024-07-01",
        "end": "2024-09-30",
        "holdings": {
            # signal_score: 0.6
            "NVDA": 0.22,
            "LITE": 0.12,
            "COHR": 0.08,
            "MU": 0.12,
            "TSM": 0.12,
            "VRT": 0.10,
            "MSFT": 0.10,
            "AVGO": 0.08,
            "XLV": 0.06,
        },
    },
    "2024Q4": {
        "name": "電力瓶頸浮現",
        "signal": "核電PPA新聞增加，資料中心電力題材",
        "start": "2024-10-01",
        "end": "2024-12-31",
        "holdings": {
            # signal_score: 0.5
            "NVDA": 0.18,
            "CEG": 0.12,
            "VST": 0.08,
            "LITE": 0.12,
            "MU": 0.10,
            "TSM": 0.12,
            "VRT": 0.10,
            "MSFT": 0.10,
            "XLV": 0.08,
        },
    },
    # ===== 2025 年 =====
    "2025Q1": {
        "name": "DeepSeek衝擊+關稅風險",
        "signal": "VIX飆升至28，DeepSeek衝擊AI估值，關稅風險升級",
        "start": "2025-01-01",
        "end": "2025-03-31",
        "holdings": {
            # signal_score: -0.3 (熊市防禦)
            "XLV": 0.25,    # 醫療防禦
            "XLU": 0.20,    # 公用事業防禦
            "CEG": 0.15,    # 電力 (AI需求不變)
            "MSFT": 0.12,   # 軟體抗關稅
            "GOOGL": 0.10,
            "SHY": 0.10,    # 短債避險
            "NVDA": 0.08,   # 大幅減碼
        },
    },
    # ===== 2025 Q2-Q4 (未來預測，僅供參考) =====
    "2025Q2": {
        "name": "關稅談判觀望",
        "signal": "[預測] 關稅影響待釐清，維持保守配置",
        "start": "2025-04-01",
        "end": "2025-06-30",
        "holdings": {
            # signal_score: -0.1 (中性偏保守)
            "XLV": 0.15,    # 維持防禦
            "CEG": 0.15,    # 電力需求穩定
            "MSFT": 0.15,   # 軟體抗關稅
            "LITE": 0.12,   # 矽光子
            "NVDA": 0.12,   # 逐步加回
            "GOOGL": 0.10,
            "MU": 0.10,
            "TSM": 0.06,
            "XLU": 0.05,
        },
    },
    "2025Q3": {
        "name": "HBM4量產預期",
        "signal": "[預測] 若HBM4如期量產，記憶體股可能領漲",
        "start": "2025-07-01",
        "end": "2025-09-30",
        "holdings": {
            "MU": 0.25,
            "NVDA": 0.20,
            "LITE": 0.15,
            "MRVL": 0.10,
            "TSM": 0.10,
            "CEG": 0.10,
            "VRT": 0.10,
        },
    },
    "2025Q4": {
        "name": "AI全面滲透預期",
        "signal": "[預測] 若AI應用持續擴散，產業鏈全面受惠",
        "start": "2025-10-01",
        "end": "2025-12-31",
        "holdings": {
            "LITE": 0.15,
            "MU": 0.15,
            "NVDA": 0.15,
            "CEG": 0.15,
            "MRVL": 0.10,
            "VRT": 0.10,
            "TSM": 0.10,
            "SMR": 0.10,
        },
    },
    "2026Q1": {
        "name": "次世代佈局預期",
        "signal": "[預測] Rubin架構預熱，SMR核電佈局",
        "start": "2026-01-01",
        "end": "2026-03-31",
        "holdings": {
            "NVDA": 0.20,
            "LITE": 0.15,
            "MU": 0.15,
            "CEG": 0.10,
            "SMR": 0.10,
            "MRVL": 0.10,
            "TSM": 0.10,
            "VRT": 0.10,
        },
    },
}

# 基準指數
BENCHMARK_SYMBOLS = {
    "SPY": "S&P 500",
    "QQQ": "Nasdaq 100",
    "SOXX": "半導體ETF",
    "SMH": "VanEck半導體",
}

# ========== 規則化信號系統 ==========
# 定義信號規則 - 每條規則基於月初可得資訊
SIGNAL_RULES = {
    # SPY 相對 200MA 位置 (最重要的趨勢指標)
    "spy_below_200ma": {"weight": -0.30, "description": "SPY 低於 200MA"},
    "spy_above_200ma": {"weight": +0.15, "description": "SPY 高於 200MA"},
    "spy_far_below_200ma": {"weight": -0.20, "description": "SPY 低於 200MA 超過 5%"},

    # VIX 恐慌指數
    "vix_extreme": {"weight": -0.25, "description": "VIX > 35 (極度恐慌)"},
    "vix_high": {"weight": -0.15, "description": "VIX 25-35 (恐慌)"},
    "vix_elevated": {"weight": -0.05, "description": "VIX 20-25 (警戒)"},
    "vix_low": {"weight": +0.10, "description": "VIX < 15 (平靜)"},

    # SPY 動能 (近期表現)
    "spy_momentum_negative": {"weight": -0.15, "description": "SPY 近月跌幅 > 5%"},
    "spy_momentum_positive": {"weight": +0.10, "description": "SPY 近月漲幅 > 3%"},

    # 200MA 斜率
    "ma200_declining": {"weight": -0.10, "description": "200MA 下降趨勢"},
    "ma200_rising": {"weight": +0.05, "description": "200MA 上升趨勢"},
}


@st.cache_data(ttl=86400)  # 快取一天
def fetch_market_indicators(start_date: str, end_date: str) -> pd.DataFrame:
    """取得市場指標數據 (SPY, VIX)"""
    import yfinance as yf

    # 取得 SPY 和 VIX
    spy = yf.Ticker("SPY")
    vix = yf.Ticker("^VIX")

    spy_hist = spy.history(start=start_date, end=end_date)
    vix_hist = vix.history(start=start_date, end=end_date)

    if spy_hist.empty:
        return pd.DataFrame()

    # 合併數據
    df = pd.DataFrame()
    df["spy_close"] = spy_hist["Close"]
    df["spy_ma200"] = spy_hist["Close"].rolling(window=200, min_periods=50).mean()
    df["spy_ma50"] = spy_hist["Close"].rolling(window=50, min_periods=20).mean()
    df["vix"] = vix_hist["Close"].reindex(df.index, method="ffill")

    # 計算衍生指標
    df["spy_vs_ma200_pct"] = (df["spy_close"] / df["spy_ma200"] - 1) * 100
    df["spy_momentum_1m"] = df["spy_close"].pct_change(periods=21) * 100  # 約一個月
    df["ma200_slope"] = df["spy_ma200"].pct_change(periods=21) * 100

    return df


def calculate_signal_score(row: pd.Series) -> tuple[float, list[str]]:
    """根據規則計算單日信號分數"""
    score = 0.0
    triggered_rules = []

    spy_vs_ma200 = row.get("spy_vs_ma200_pct", 0)
    vix = row.get("vix", 20)
    momentum = row.get("spy_momentum_1m", 0)
    ma200_slope = row.get("ma200_slope", 0)

    # SPY vs 200MA
    if pd.notna(spy_vs_ma200):
        if spy_vs_ma200 < -5:
            score += SIGNAL_RULES["spy_far_below_200ma"]["weight"]
            score += SIGNAL_RULES["spy_below_200ma"]["weight"]
            triggered_rules.append("SPY遠低於200MA")
        elif spy_vs_ma200 < 0:
            score += SIGNAL_RULES["spy_below_200ma"]["weight"]
            triggered_rules.append("SPY低於200MA")
        else:
            score += SIGNAL_RULES["spy_above_200ma"]["weight"]
            triggered_rules.append("SPY高於200MA")

    # VIX
    if pd.notna(vix):
        if vix > 35:
            score += SIGNAL_RULES["vix_extreme"]["weight"]
            triggered_rules.append(f"VIX極高({vix:.0f})")
        elif vix > 25:
            score += SIGNAL_RULES["vix_high"]["weight"]
            triggered_rules.append(f"VIX偏高({vix:.0f})")
        elif vix > 20:
            score += SIGNAL_RULES["vix_elevated"]["weight"]
            triggered_rules.append(f"VIX警戒({vix:.0f})")
        elif vix < 15:
            score += SIGNAL_RULES["vix_low"]["weight"]
            triggered_rules.append(f"VIX低檔({vix:.0f})")

    # Momentum
    if pd.notna(momentum):
        if momentum < -5:
            score += SIGNAL_RULES["spy_momentum_negative"]["weight"]
            triggered_rules.append(f"動能負({momentum:.1f}%)")
        elif momentum > 3:
            score += SIGNAL_RULES["spy_momentum_positive"]["weight"]
            triggered_rules.append(f"動能正({momentum:.1f}%)")

    # 200MA 斜率
    if pd.notna(ma200_slope):
        if ma200_slope < -0.5:
            score += SIGNAL_RULES["ma200_declining"]["weight"]
            triggered_rules.append("200MA下降")
        elif ma200_slope > 0.5:
            score += SIGNAL_RULES["ma200_rising"]["weight"]
            triggered_rules.append("200MA上升")

    # 限制在 -1 到 1 之間
    score = max(-1.0, min(1.0, score))

    return score, triggered_rules


@st.cache_data(ttl=86400)
def calculate_monthly_signals(start_year: int = 2022, end_year: int = 2026) -> dict:
    """計算每月初的信號分數"""
    # 取得足夠的歷史數據 (需要200天MA)
    start_date = f"{start_year - 1}-01-01"
    end_date = f"{end_year}-12-31"

    df = fetch_market_indicators(start_date, end_date)

    if df.empty:
        return {}

    monthly_signals = {}

    # 對每個月，取月初第一個交易日的數據
    for year in range(start_year, end_year + 1):
        for month in range(1, 13):
            month_key = f"{year}-{month:02d}"

            # 找該月第一個交易日
            month_start = f"{year}-{month:02d}-01"
            if month == 12:
                month_end = f"{year + 1}-01-01"
            else:
                month_end = f"{year}-{month + 1:02d}-01"

            month_data = df[(df.index >= month_start) & (df.index < month_end)]

            if month_data.empty:
                continue

            # 取月初第一個交易日
            first_day = month_data.iloc[0]
            score, rules = calculate_signal_score(first_day)

            monthly_signals[month_key] = {
                "date": month_data.index[0].strftime("%Y-%m-%d"),
                "score": round(score, 2),
                "rules": rules,
                "spy_close": round(first_day.get("spy_close", 0), 2),
                "spy_vs_ma200": round(first_day.get("spy_vs_ma200_pct", 0), 2),
                "vix": round(first_day.get("vix", 0), 1),
            }

    return monthly_signals


def get_rule_based_allocation(signal_score: float) -> dict:
    """根據信號分數決定配置風格"""
    if signal_score <= -0.4:
        return {
            "style": "極度防禦",
            "equity_pct": 20,
            "preferred": ["SHY", "XLV", "XLU", "COST", "JNJ", "PG"],
        }
    elif signal_score <= -0.2:
        return {
            "style": "防禦",
            "equity_pct": 40,
            "preferred": ["XLV", "XLU", "CEG", "COST", "MSFT", "SHY"],
        }
    elif signal_score <= 0.1:
        return {
            "style": "中性",
            "equity_pct": 60,
            "preferred": ["MSFT", "GOOGL", "XLV", "CEG", "NVDA", "AAPL"],
        }
    elif signal_score <= 0.3:
        return {
            "style": "偏多",
            "equity_pct": 75,
            "preferred": ["NVDA", "MSFT", "GOOGL", "META", "TSM", "AMD"],
        }
    else:
        return {
            "style": "積極",
            "equity_pct": 90,
            "preferred": ["NVDA", "LITE", "MRVL", "TSM", "AMD", "MU"],
        }


# ========== 月度持股池 (2022-2026) ==========
# 基於每月初可得信號的配置 (holdings 仍手動維護，signal_score 可由規則系統覆蓋)
MONTHLY_PORTFOLIOS = {
    # ===== 2022 年 =====
    "2022-01": {
        "signal": "Fed轉鷹，CPI 7%，SPY仍在高檔",
        "signal_score": 0.2,
        "holdings": {"MSFT": 0.18, "AAPL": 0.15, "GOOGL": 0.12, "NVDA": 0.10, "XLV": 0.12, "XLF": 0.10, "JPM": 0.08, "XLE": 0.08, "COST": 0.07},
    },
    "2022-02": {
        "signal": "俄烏戰爭爆發，VIX飆升，油價大漲",
        "signal_score": -0.3,
        "holdings": {"XLE": 0.20, "XLV": 0.18, "XLU": 0.15, "COST": 0.12, "JNJ": 0.10, "PG": 0.10, "SHY": 0.08, "JPM": 0.07},
    },
    "2022-03": {
        "signal": "Fed首次升息25bp，通膨持續上升",
        "signal_score": -0.2,
        "holdings": {"XLE": 0.20, "XLV": 0.15, "XLU": 0.12, "XLF": 0.12, "COST": 0.10, "JNJ": 0.10, "PG": 0.08, "JPM": 0.08, "SHY": 0.05},
    },
    "2022-04": {
        "signal": "CPI 8.5%創新高，Fed暗示50bp",
        "signal_score": -0.4,
        "holdings": {"XLE": 0.22, "XLV": 0.18, "XLU": 0.15, "SHY": 0.12, "COST": 0.10, "JNJ": 0.10, "PG": 0.08, "UNH": 0.05},
    },
    "2022-05": {
        "signal": "Fed升息50bp，QT開始，SPY跌破200MA",
        "signal_score": -0.5,
        "holdings": {"XLE": 0.20, "XLV": 0.18, "SHY": 0.18, "XLU": 0.15, "COST": 0.10, "JNJ": 0.10, "PG": 0.09},
    },
    "2022-06": {
        "signal": "CPI 9.1%峰值！Fed升息75bp",
        "signal_score": -0.6,
        "holdings": {"SHY": 0.25, "XLE": 0.18, "XLV": 0.18, "XLU": 0.15, "COST": 0.10, "JNJ": 0.08, "PG": 0.06},
    },
    "2022-07": {
        "signal": "技術性反彈，CPI仍高",
        "signal_score": -0.3,
        "holdings": {"XLE": 0.18, "XLV": 0.18, "SHY": 0.15, "XLU": 0.12, "COST": 0.10, "MSFT": 0.08, "AAPL": 0.08, "JNJ": 0.06, "PG": 0.05},
    },
    "2022-08": {
        "signal": "Jackson Hole鷹派發言，反彈結束",
        "signal_score": -0.5,
        "holdings": {"SHY": 0.22, "XLV": 0.18, "XLE": 0.15, "XLU": 0.15, "COST": 0.10, "JNJ": 0.10, "PG": 0.10},
    },
    "2022-09": {
        "signal": "Fed升息75bp，暗示更高利率",
        "signal_score": -0.6,
        "holdings": {"SHY": 0.28, "XLV": 0.18, "XLU": 0.18, "XLE": 0.12, "COST": 0.10, "JNJ": 0.08, "PG": 0.06},
    },
    "2022-10": {
        "signal": "SPY接近年度低點，VIX高檔",
        "signal_score": -0.4,
        "holdings": {"SHY": 0.22, "XLV": 0.18, "XLU": 0.15, "XLE": 0.12, "COST": 0.10, "MSFT": 0.08, "AAPL": 0.08, "JNJ": 0.07},
    },
    "2022-11": {
        "signal": "CPI首次放緩至7.7%，市場反彈",
        "signal_score": -0.1,
        "holdings": {"XLV": 0.15, "MSFT": 0.12, "AAPL": 0.12, "XLE": 0.12, "XLU": 0.10, "COST": 0.10, "GOOGL": 0.08, "SHY": 0.08, "JPM": 0.08, "JNJ": 0.05},
    },
    "2022-12": {
        "signal": "Fed升息50bp放緩，年底盤整",
        "signal_score": 0.0,
        "holdings": {"XLV": 0.15, "MSFT": 0.12, "AAPL": 0.12, "XLE": 0.10, "XLU": 0.10, "COST": 0.10, "GOOGL": 0.08, "JPM": 0.08, "META": 0.08, "JNJ": 0.07},
    },
    # ===== 2023 年 =====
    "2023-01": {
        "signal": "新年樂觀情緒，CPI持續下降",
        "signal_score": 0.2,
        "holdings": {"MSFT": 0.15, "AAPL": 0.12, "GOOGL": 0.12, "META": 0.10, "XLV": 0.12, "NVDA": 0.08, "XLE": 0.08, "JPM": 0.08, "COST": 0.08, "AMD": 0.07},
    },
    "2023-02": {
        "signal": "就業數據強勁，升息擔憂回升",
        "signal_score": 0.0,
        "holdings": {"MSFT": 0.14, "AAPL": 0.12, "GOOGL": 0.10, "XLV": 0.12, "META": 0.10, "XLE": 0.10, "JPM": 0.08, "COST": 0.08, "NVDA": 0.08, "XLU": 0.08},
    },
    "2023-03": {
        "signal": "SVB倒閉！銀行危機爆發",
        "signal_score": -0.4,
        "holdings": {"SHY": 0.20, "XLV": 0.18, "XLU": 0.15, "MSFT": 0.12, "AAPL": 0.10, "GOOGL": 0.08, "COST": 0.08, "JNJ": 0.05, "PG": 0.04},
    },
    "2023-04": {
        "signal": "銀行危機緩和，Fed暫停預期",
        "signal_score": 0.1,
        "holdings": {"MSFT": 0.15, "AAPL": 0.12, "GOOGL": 0.12, "XLV": 0.12, "META": 0.10, "NVDA": 0.10, "COST": 0.08, "XLU": 0.08, "AMD": 0.08, "SHY": 0.05},
    },
    "2023-05": {
        "signal": "AI熱潮初現！NVDA財報驚艷",
        "signal_score": 0.5,
        "holdings": {"NVDA": 0.22, "MSFT": 0.15, "GOOGL": 0.12, "META": 0.12, "AAPL": 0.10, "AMD": 0.10, "TSM": 0.08, "AVGO": 0.06, "XLV": 0.05},
    },
    "2023-06": {
        "signal": "AI題材持續發酵，Fed跳過升息",
        "signal_score": 0.6,
        "holdings": {"NVDA": 0.25, "MSFT": 0.15, "GOOGL": 0.12, "META": 0.10, "AMD": 0.10, "TSM": 0.08, "AVGO": 0.08, "AAPL": 0.07, "MU": 0.05},
    },
    "2023-07": {
        "signal": "科技股續強，CPI降至3%",
        "signal_score": 0.6,
        "holdings": {"NVDA": 0.25, "MSFT": 0.15, "GOOGL": 0.12, "META": 0.10, "AMD": 0.10, "TSM": 0.08, "AVGO": 0.08, "MU": 0.07, "AAPL": 0.05},
    },
    "2023-08": {
        "signal": "中國經濟擔憂，美債殖利率飆升",
        "signal_score": 0.2,
        "holdings": {"NVDA": 0.18, "MSFT": 0.15, "XLV": 0.12, "GOOGL": 0.10, "META": 0.10, "AAPL": 0.08, "AMD": 0.08, "TSM": 0.07, "XLU": 0.07, "AVGO": 0.05},
    },
    "2023-09": {
        "signal": "Higher for longer，10Y破4.5%",
        "signal_score": -0.1,
        "holdings": {"XLV": 0.15, "NVDA": 0.15, "MSFT": 0.12, "XLU": 0.10, "GOOGL": 0.10, "META": 0.08, "AAPL": 0.08, "COST": 0.08, "SHY": 0.07, "AMD": 0.07},
    },
    "2023-10": {
        "signal": "以巴衝突，10Y觸4.9%高點",
        "signal_score": -0.2,
        "holdings": {"XLV": 0.18, "XLU": 0.12, "MSFT": 0.12, "NVDA": 0.12, "SHY": 0.10, "GOOGL": 0.10, "COST": 0.08, "META": 0.08, "AAPL": 0.05, "JNJ": 0.05},
    },
    "2023-11": {
        "signal": "Fed暗示停止升息，殖利率回落",
        "signal_score": 0.4,
        "holdings": {"NVDA": 0.20, "MSFT": 0.15, "GOOGL": 0.12, "META": 0.12, "AMD": 0.10, "TSM": 0.08, "AVGO": 0.08, "AAPL": 0.08, "MU": 0.07},
    },
    "2023-12": {
        "signal": "Fed暗示2024降息，聖誕行情",
        "signal_score": 0.6,
        "holdings": {"NVDA": 0.22, "MSFT": 0.15, "META": 0.12, "GOOGL": 0.12, "AMD": 0.10, "TSM": 0.08, "AVGO": 0.08, "MU": 0.08, "AAPL": 0.05},
    },
    # ===== 2024 年 =====
    "2024-01": {
        "signal": "AI CapEx指引強勁，VIX低檔",
        "signal_score": 0.7,
        "holdings": {"NVDA": 0.30, "MSFT": 0.18, "TSM": 0.12, "AVGO": 0.12, "AMD": 0.10, "GOOGL": 0.10, "META": 0.08},
    },
    "2024-02": {
        "signal": "NVDA財報超預期，AI需求確認",
        "signal_score": 0.7,
        "holdings": {"NVDA": 0.32, "MSFT": 0.16, "TSM": 0.12, "AVGO": 0.12, "AMD": 0.10, "GOOGL": 0.10, "META": 0.08},
    },
    "2024-03": {
        "signal": "GTC大會，Blackwell發布",
        "signal_score": 0.7,
        "holdings": {"NVDA": 0.30, "MSFT": 0.15, "TSM": 0.12, "AVGO": 0.12, "MU": 0.10, "AMD": 0.08, "GOOGL": 0.08, "META": 0.05},
    },
    "2024-04": {
        "signal": "HBM供需緊張浮現",
        "signal_score": 0.6,
        "holdings": {"NVDA": 0.28, "MU": 0.15, "TSM": 0.12, "MSFT": 0.12, "AVGO": 0.10, "AMD": 0.08, "GOOGL": 0.08, "XLV": 0.07},
    },
    "2024-05": {
        "signal": "記憶體題材持續",
        "signal_score": 0.6,
        "holdings": {"NVDA": 0.25, "MU": 0.18, "TSM": 0.12, "MSFT": 0.12, "AVGO": 0.10, "AMD": 0.08, "GOOGL": 0.08, "XLV": 0.07},
    },
    "2024-06": {
        "signal": "NVDA成全球市值最大",
        "signal_score": 0.6,
        "holdings": {"NVDA": 0.25, "MU": 0.15, "TSM": 0.12, "MSFT": 0.12, "AVGO": 0.10, "LITE": 0.08, "AMD": 0.08, "GOOGL": 0.05, "XLV": 0.05},
    },
    "2024-07": {
        "signal": "800G量產，光互連題材",
        "signal_score": 0.6,
        "holdings": {"NVDA": 0.22, "LITE": 0.12, "MU": 0.12, "TSM": 0.12, "MSFT": 0.10, "COHR": 0.08, "VRT": 0.08, "AVGO": 0.08, "XLV": 0.08},
    },
    "2024-08": {
        "signal": "矽光子題材擴散",
        "signal_score": 0.5,
        "holdings": {"NVDA": 0.20, "LITE": 0.15, "MU": 0.12, "TSM": 0.10, "VRT": 0.10, "COHR": 0.08, "MSFT": 0.08, "AVGO": 0.07, "XLV": 0.10},
    },
    "2024-09": {
        "signal": "Fed降息預期，光互連持續",
        "signal_score": 0.5,
        "holdings": {"NVDA": 0.18, "LITE": 0.15, "CEG": 0.10, "MU": 0.10, "TSM": 0.10, "VRT": 0.10, "MSFT": 0.10, "COHR": 0.07, "XLV": 0.10},
    },
    "2024-10": {
        "signal": "核電PPA簽約消息，電力題材",
        "signal_score": 0.5,
        "holdings": {"CEG": 0.15, "NVDA": 0.15, "LITE": 0.12, "VST": 0.08, "MU": 0.10, "TSM": 0.10, "VRT": 0.10, "MSFT": 0.10, "XLV": 0.10},
    },
    "2024-11": {
        "signal": "川普當選，關稅擔憂初現",
        "signal_score": 0.3,
        "holdings": {"CEG": 0.15, "NVDA": 0.12, "LITE": 0.12, "XLV": 0.15, "MSFT": 0.12, "MU": 0.08, "VRT": 0.08, "TSM": 0.08, "GOOGL": 0.10},
    },
    "2024-12": {
        "signal": "年底獲利了結，估值擔憂",
        "signal_score": 0.2,
        "holdings": {"XLV": 0.15, "CEG": 0.15, "MSFT": 0.12, "NVDA": 0.12, "LITE": 0.10, "GOOGL": 0.10, "XLU": 0.08, "VRT": 0.08, "MU": 0.05, "SHY": 0.05},
    },
    # ===== 2025 年 =====
    "2025-01": {
        "signal": "DeepSeek衝擊！VIX飆升至28",
        "signal_score": -0.4,
        "holdings": {"XLV": 0.25, "XLU": 0.20, "SHY": 0.15, "CEG": 0.12, "MSFT": 0.10, "GOOGL": 0.08, "NVDA": 0.05, "LITE": 0.05},
    },
    "2025-02": {
        "signal": "關稅威脅升級，市場震盪",
        "signal_score": -0.3,
        "holdings": {"XLV": 0.22, "XLU": 0.18, "CEG": 0.15, "SHY": 0.12, "MSFT": 0.12, "GOOGL": 0.08, "NVDA": 0.08, "LITE": 0.05},
    },
    "2025-03": {
        "signal": "關稅談判中，維持觀望",
        "signal_score": -0.2,
        "holdings": {"XLV": 0.18, "CEG": 0.15, "XLU": 0.15, "MSFT": 0.12, "NVDA": 0.10, "GOOGL": 0.10, "LITE": 0.08, "SHY": 0.07, "VRT": 0.05},
    },
    # ===== 2025 Q2 (關稅風暴) =====
    "2025-04": {
        "signal": "4/2解放日關稅！SPY暴跌，VIX飆至45",
        "signal_score": -0.6,
        "holdings": {"SHY": 0.30, "XLV": 0.20, "XLU": 0.18, "COST": 0.10, "JNJ": 0.08, "PG": 0.07, "CEG": 0.07},
    },
    "2025-05": {
        "signal": "關稅談判反覆，市場劇烈震盪",
        "signal_score": -0.4,
        "holdings": {"SHY": 0.22, "XLV": 0.20, "XLU": 0.15, "CEG": 0.12, "COST": 0.10, "MSFT": 0.08, "JNJ": 0.08, "PG": 0.05},
    },
    "2025-06": {
        "signal": "部分關稅暫緩90天，市場喘息",
        "signal_score": -0.2,
        "holdings": {"XLV": 0.18, "CEG": 0.15, "XLU": 0.12, "SHY": 0.12, "MSFT": 0.12, "NVDA": 0.08, "GOOGL": 0.08, "LITE": 0.08, "COST": 0.07},
    },
    # ===== 2025 Q3 (謹慎復甦) =====
    "2025-07": {
        "signal": "關稅不確定性持續，觀望Q2財報",
        "signal_score": -0.1,
        "holdings": {"XLV": 0.15, "CEG": 0.15, "MSFT": 0.12, "NVDA": 0.10, "XLU": 0.10, "GOOGL": 0.10, "LITE": 0.08, "SHY": 0.08, "MU": 0.07, "VRT": 0.05},
    },
    "2025-08": {
        "signal": "AI CapEx確認持續，科技股回穩",
        "signal_score": 0.1,
        "holdings": {"NVDA": 0.15, "MSFT": 0.12, "CEG": 0.12, "LITE": 0.12, "XLV": 0.10, "GOOGL": 0.10, "MU": 0.08, "MRVL": 0.08, "TSM": 0.08, "VRT": 0.05},
    },
    "2025-09": {
        "signal": "Fed維持利率，關稅談判有進展",
        "signal_score": 0.2,
        "holdings": {"NVDA": 0.15, "LITE": 0.15, "MSFT": 0.12, "CEG": 0.10, "MRVL": 0.10, "MU": 0.10, "GOOGL": 0.08, "TSM": 0.08, "XLV": 0.07, "VRT": 0.05},
    },
    # ===== 2025 Q4 (逐步回穩) =====
    "2025-10": {
        "signal": "Q3財報優於預期，CPO題材發酵",
        "signal_score": 0.3,
        "holdings": {"LITE": 0.18, "NVDA": 0.15, "MRVL": 0.12, "CEG": 0.10, "MSFT": 0.10, "MU": 0.10, "COHR": 0.08, "TSM": 0.07, "GOOGL": 0.05, "VRT": 0.05},
    },
    "2025-11": {
        "signal": "市場回穩，年底行情啟動",
        "signal_score": 0.4,
        "holdings": {"LITE": 0.18, "NVDA": 0.15, "MRVL": 0.12, "MU": 0.10, "CEG": 0.10, "MSFT": 0.10, "COHR": 0.08, "TSM": 0.07, "GOOGL": 0.05, "META": 0.05},
    },
    "2025-12": {
        "signal": "聖誕行情，但關稅仍是變數",
        "signal_score": 0.3,
        "holdings": {"NVDA": 0.15, "LITE": 0.15, "MRVL": 0.10, "MSFT": 0.10, "CEG": 0.10, "MU": 0.10, "XLV": 0.08, "TSM": 0.08, "COHR": 0.07, "GOOGL": 0.07},
    },
    # ===== 2026 年 =====
    "2026-01": {
        "signal": "新年展望，關稅政策待觀察",
        "signal_score": 0.2,
        "holdings": {"NVDA": 0.15, "LITE": 0.12, "MSFT": 0.12, "CEG": 0.10, "XLV": 0.12, "MU": 0.10, "MRVL": 0.08, "TSM": 0.08, "GOOGL": 0.08, "XLU": 0.05},
    },
    "2026-02": {
        "signal": "SPY穩站200MA上方，VIX低檔，偏多操作",
        "signal_score": 0.2,
        "holdings": {"NVDA": 0.18, "LITE": 0.15, "MRVL": 0.12, "MU": 0.10, "CEG": 0.10, "MSFT": 0.10, "TSM": 0.08, "COHR": 0.07, "GOOGL": 0.05, "XLV": 0.05},
    },
}

def get_monthly_periods(start_month: str, end_month: str) -> list:
    """取得月份列表"""
    months = list(MONTHLY_PORTFOLIOS.keys())
    try:
        start_idx = months.index(start_month)
        end_idx = months.index(end_month)
        return months[start_idx:end_idx+1]
    except ValueError:
        return []


@st.cache_data(ttl=3600)
def fetch_stock_prices(symbols: list, start_date: str, end_date: str) -> pd.DataFrame:
    """取得股票歷史價格"""
    import yfinance as yf

    all_data = {}
    for symbol in symbols:
        try:
            ticker = yf.Ticker(symbol)
            df = ticker.history(start=start_date, end=end_date)
            if not df.empty:
                all_data[symbol] = df['Close']
        except Exception as e:
            st.warning(f"無法取得 {symbol} 數據: {e}")

    if all_data:
        return pd.DataFrame(all_data)
    return pd.DataFrame()


def calculate_portfolio_returns(prices_df: pd.DataFrame, weights: dict) -> pd.Series:
    """計算投資組合報酬"""
    # 只使用有數據的股票
    available = [s for s in weights.keys() if s in prices_df.columns]
    if not available:
        return pd.Series()

    # 重新正規化權重
    total_weight = sum(weights[s] for s in available)
    norm_weights = {s: weights[s] / total_weight for s in available}

    # 計算日報酬
    returns = prices_df[available].pct_change()

    # 加權報酬
    portfolio_returns = pd.Series(0, index=returns.index)
    for symbol, weight in norm_weights.items():
        portfolio_returns += returns[symbol] * weight

    return portfolio_returns


def calculate_metrics(returns: pd.Series) -> dict:
    """計算績效指標"""
    if returns.empty or len(returns) < 2:
        return {"total_return": 0, "annualized_return": 0, "volatility": 0,
                "sharpe": 0, "max_drawdown": 0, "win_rate": 0}

    returns = returns.dropna()
    if len(returns) < 2:
        return {"total_return": 0, "annualized_return": 0, "volatility": 0,
                "sharpe": 0, "max_drawdown": 0, "win_rate": 0}

    # 總報酬
    cumulative = (1 + returns).cumprod()
    total_return = (cumulative.iloc[-1] - 1) * 100

    # 年化報酬 (假設252交易日)
    days = len(returns)
    annualized_return = ((1 + total_return/100) ** (252/days) - 1) * 100 if days > 0 else 0

    # 波動率 (年化)
    volatility = returns.std() * (252 ** 0.5) * 100

    # 夏普比率 (假設無風險利率 4%)
    risk_free = 0.04 / 252
    excess_returns = returns - risk_free
    sharpe = (excess_returns.mean() / returns.std()) * (252 ** 0.5) if returns.std() > 0 else 0

    # 最大回撤
    cummax = cumulative.cummax()
    drawdown = (cumulative - cummax) / cummax
    max_drawdown = drawdown.min() * 100

    # 勝率
    win_rate = (returns > 0).sum() / len(returns) * 100

    return {
        "total_return": total_return,
        "annualized_return": annualized_return,
        "volatility": volatility,
        "sharpe": sharpe,
        "max_drawdown": max_drawdown,
        "win_rate": win_rate,
    }


def render_quarterly_backtest_page():
    """渲染持股池回測頁面"""
    st.title("📊 持股池回測系統")
    st.markdown("**基於趨勢雷達分析的投資組合回測**")

    # 換股頻率選擇
    freq_tab1, freq_tab2, freq_tab3 = st.tabs(["📅 季度換股", "📆 月度換股", "📐 規則信號"])

    with freq_tab1:
        render_quarterly_backtest()

    with freq_tab2:
        render_monthly_backtest()

    with freq_tab3:
        render_rule_based_signals()


def render_quarterly_backtest():
    """季度回測"""
    st.markdown("#### 季度換股回測 (2022-2026)")

    # 選擇回測範圍
    col1, col2 = st.columns(2)
    with col1:
        quarters = list(QUARTERLY_PORTFOLIOS.keys())
        start_q = st.selectbox("起始季度", quarters, index=0, key="q_start")
    with col2:
        start_idx = quarters.index(start_q)
        end_q = st.selectbox("結束季度", quarters[start_idx:], index=len(quarters[start_idx:])-1, key="q_end")

    # 選擇基準
    benchmark = st.selectbox(
        "比較基準",
        list(BENCHMARK_SYMBOLS.keys()),
        format_func=lambda x: f"{x} - {BENCHMARK_SYMBOLS[x]}",
        key="q_bench"
    )

    # 策略選擇
    st.markdown("---")
    st.markdown("#### 🎯 熊市策略選擇")

    strategy = st.radio(
        "當信號分數 ≤ -0.2 (熊市信號) 時：",
        ["🛡️ 熊市防禦", "💵 熊市空手", "📊 兩者比較"],
        index=2,
        horizontal=True,
        help="熊市防禦：持有防禦股(XLV/XLU/SHY)；熊市空手：100%現金(SHY)",
        key="q_strategy"
    )

    # 空手閾值
    if strategy in ["💵 熊市空手", "📊 兩者比較"]:
        cash_threshold = st.slider(
            "空手信號閾值",
            min_value=-0.5,
            max_value=0.0,
            value=-0.2,
            step=0.1,
            help="信號分數低於此值時，轉為100%現金",
            key="q_threshold"
        )
    else:
        cash_threshold = -0.2

    if st.button("🚀 開始季度回測", type="primary", use_container_width=True, key="q_run"):
        with st.spinner("正在取得數據並計算..."):
            run_backtest(start_q, end_q, benchmark, strategy, cash_threshold)


def render_monthly_backtest():
    """月度回測"""
    st.markdown("#### 月度換股回測 (2022-2026)")
    st.info("💡 月度換股更靈活，適合主動管理。每月初根據信號調整持股。")

    # 信號來源選擇
    signal_source = st.radio(
        "📡 信號來源",
        ["📝 手動信號 (人工判斷)", "📐 規則信號 (自動計算)"],
        index=0,
        horizontal=True,
        help="手動信號：使用預設的 signal_score；規則信號：根據 SPY/VIX 等指標自動計算",
        key="m_signal_source"
    )
    use_rule_signals = signal_source == "📐 規則信號 (自動計算)"

    # 選擇回測範圍
    col1, col2 = st.columns(2)
    months = list(MONTHLY_PORTFOLIOS.keys())
    with col1:
        start_m = st.selectbox("起始月份", months, index=0, key="m_start")
    with col2:
        start_idx = months.index(start_m)
        end_m = st.selectbox("結束月份", months[start_idx:], index=len(months[start_idx:])-1, key="m_end")

    # 選擇基準
    benchmark = st.selectbox(
        "比較基準",
        list(BENCHMARK_SYMBOLS.keys()),
        format_func=lambda x: f"{x} - {BENCHMARK_SYMBOLS[x]}",
        key="m_bench"
    )

    # 熊市策略
    st.markdown("---")
    strategy = st.radio(
        "當信號分數 ≤ -0.2 (熊市信號) 時：",
        ["🛡️ 熊市防禦", "💵 熊市空手", "📊 兩者比較"],
        index=2,
        horizontal=True,
        help="熊市防禦：按配置持有防禦股；熊市空手：100%現金(SHY)",
        key="m_strategy"
    )

    # 空手閾值
    if strategy in ["💵 熊市空手", "📊 兩者比較"]:
        cash_threshold = st.slider(
            "空手信號閾值",
            min_value=-0.5,
            max_value=0.0,
            value=-0.2,
            step=0.1,
            help="信號分數低於此值時，轉為100%現金",
            key="m_threshold"
        )
    else:
        cash_threshold = -0.2

    col1, col2 = st.columns(2)
    with col1:
        if st.button("🚀 開始月度回測", type="primary", use_container_width=True, key="m_run"):
            with st.spinner("正在取得數據並計算..."):
                run_monthly_backtest(start_m, end_m, benchmark, strategy, cash_threshold, use_rule_signals)

    with col2:
        if st.button("📋 查看本月建議", use_container_width=True, key="m_suggest"):
            show_current_month_suggestion()


def show_current_month_suggestion():
    """顯示當月換股建議"""
    from datetime import datetime
    current_month = datetime.now().strftime("%Y-%m")

    st.markdown("---")
    st.markdown("### 📋 當月持股建議")

    if current_month in MONTHLY_PORTFOLIOS:
        m_info = MONTHLY_PORTFOLIOS[current_month]
        signal_score = m_info["signal_score"]

        # 信號燈號
        if signal_score >= 0.5:
            light = "🟢 積極"
        elif signal_score >= 0.2:
            light = "🟡 偏多"
        elif signal_score >= -0.2:
            light = "⚪ 中性"
        elif signal_score >= -0.4:
            light = "🟠 偏空"
        else:
            light = "🔴 防禦"

        st.markdown(f"**月份**: {current_month}")
        st.markdown(f"**信號**: {m_info['signal']}")
        st.markdown(f"**信號分數**: {signal_score:+.1f} ({light})")

        st.markdown("**建議持股配置:**")
        holdings_df = pd.DataFrame([
            {
                "股票": s,
                "權重": f"{w*100:.0f}%",
                "公司": STOCK_DETAILS.get(s, {}).get("name", s)
            }
            for s, w in sorted(m_info["holdings"].items(), key=lambda x: x[1], reverse=True)
        ])
        st.dataframe(holdings_df, use_container_width=True, hide_index=True)

        # 下月預覽
        next_month_candidates = [m for m in MONTHLY_PORTFOLIOS.keys() if m > current_month]
        if next_month_candidates:
            next_month = next_month_candidates[0]
            next_info = MONTHLY_PORTFOLIOS[next_month]
            with st.expander(f"📅 下月預覽 ({next_month})"):
                st.markdown(f"**信號**: {next_info['signal']}")
                st.markdown(f"**信號分數**: {next_info['signal_score']:+.1f}")
    else:
        # 找最近的月份
        past_months = [m for m in MONTHLY_PORTFOLIOS.keys() if m <= current_month]
        if past_months:
            latest = past_months[-1]
            m_info = MONTHLY_PORTFOLIOS[latest]
            st.warning(f"當月 ({current_month}) 尚無配置，顯示最近配置 ({latest})")
            st.markdown(f"**信號**: {m_info['signal']}")

            holdings_df = pd.DataFrame([
                {"股票": s, "權重": f"{w*100:.0f}%", "公司": STOCK_DETAILS.get(s, {}).get("name", s)}
                for s, w in sorted(m_info["holdings"].items(), key=lambda x: x[1], reverse=True)
            ])
            st.dataframe(holdings_df, use_container_width=True, hide_index=True)
        else:
            st.error("無可用配置")


def render_rule_based_signals():
    """顯示規則化信號系統"""
    st.markdown("#### 📐 規則化信號系統")
    st.info("""
    **無後見之明的信號系統** - 所有信號基於月初第一個交易日可得的市場數據自動計算，
    不依賴人工判斷，避免事後諸葛的偏誤。
    """)

    # 顯示規則定義
    with st.expander("📋 信號規則定義", expanded=False):
        rules_data = []
        for rule_id, rule in SIGNAL_RULES.items():
            rules_data.append({
                "規則": rule["description"],
                "權重": f"{rule['weight']:+.2f}",
                "類型": "空方" if rule["weight"] < 0 else "多方"
            })
        st.dataframe(pd.DataFrame(rules_data), use_container_width=True, hide_index=True)

        st.markdown("""
        **計算邏輯：**
        - 每月初第一個交易日，檢查各項指標
        - 觸發的規則權重加總 = 信號分數
        - 分數範圍：-1.0 (極度看空) 到 +1.0 (極度看多)
        - 分數 ≤ -0.2 視為熊市信號
        """)

    # 計算信號
    st.markdown("---")
    col1, col2 = st.columns(2)
    with col1:
        start_year = st.selectbox("起始年份", [2022, 2023, 2024, 2025, 2026], index=0, key="rule_start_year")
    with col2:
        end_year = st.selectbox("結束年份", [2022, 2023, 2024, 2025, 2026], index=4, key="rule_end_year")

    if st.button("🔄 計算規則信號", type="primary", use_container_width=True, key="calc_rules"):
        with st.spinner("正在取得市場數據並計算信號..."):
            signals = calculate_monthly_signals(start_year, end_year)

            if not signals:
                st.error("無法取得市場數據")
                return

            # 顯示信號結果
            st.markdown("### 📊 計算結果")

            # 與手動信號比較
            st.markdown("#### 規則信號 vs 手動信號")

            compare_data = []
            for month_key in sorted(signals.keys()):
                sig = signals[month_key]
                manual_score = MONTHLY_PORTFOLIOS.get(month_key, {}).get("signal_score", None)
                manual_signal = MONTHLY_PORTFOLIOS.get(month_key, {}).get("signal", "N/A")

                rule_score = sig["score"]

                # 判斷是否一致
                rule_bear = rule_score <= -0.2
                manual_bear = manual_score <= -0.2 if manual_score is not None else None

                if manual_bear is None:
                    match = "⚪"
                elif rule_bear == manual_bear:
                    match = "✅"
                else:
                    match = "❌"

                compare_data.append({
                    "月份": month_key,
                    "規則分數": f"{rule_score:+.2f}",
                    "手動分數": f"{manual_score:+.2f}" if manual_score is not None else "N/A",
                    "一致": match,
                    "觸發規則": ", ".join(sig["rules"][:3]),
                    "SPY": f"${sig['spy_close']:.0f}",
                    "vs200MA": f"{sig['spy_vs_ma200']:+.1f}%",
                    "VIX": f"{sig['vix']:.0f}",
                })

            df = pd.DataFrame(compare_data)
            st.dataframe(df, use_container_width=True, hide_index=True, height=400)

            # 統計一致性
            matches = [d["一致"] for d in compare_data if d["一致"] != "⚪"]
            if matches:
                match_rate = sum(1 for m in matches if m == "✅") / len(matches) * 100
                st.metric("熊市/多頭判斷一致率", f"{match_rate:.0f}%")

            # 繪製信號走勢圖
            st.markdown("#### 📈 信號分數走勢")
            fig = go.Figure()

            months = sorted(signals.keys())
            rule_scores = [signals[m]["score"] for m in months]

            fig.add_trace(go.Scatter(
                x=months, y=rule_scores,
                name="規則信號", mode="lines+markers",
                line=dict(color="#2196F3", width=2)
            ))

            # 手動信號
            manual_scores = [MONTHLY_PORTFOLIOS.get(m, {}).get("signal_score", None) for m in months]
            manual_scores_clean = [s if s is not None else 0 for s in manual_scores]
            fig.add_trace(go.Scatter(
                x=months, y=manual_scores_clean,
                name="手動信號", mode="lines+markers",
                line=dict(color="#FF9800", width=2, dash="dot")
            ))

            # 熊市閾值線
            fig.add_hline(y=-0.2, line_dash="dash", line_color="red", annotation_text="熊市閾值")
            fig.add_hline(y=0, line_dash="solid", line_color="gray", opacity=0.3)

            fig.update_layout(
                xaxis_title="月份",
                yaxis_title="信號分數",
                hovermode="x unified",
                height=400,
                yaxis=dict(range=[-1, 1])
            )
            st.plotly_chart(fig, use_container_width=True)

            # 顯示不一致的月份
            disagreements = [d for d in compare_data if d["一致"] == "❌"]
            if disagreements:
                st.markdown("#### ⚠️ 判斷不一致的月份")
                st.dataframe(pd.DataFrame(disagreements), use_container_width=True, hide_index=True)


def run_monthly_backtest(start_m: str, end_m: str, benchmark: str, strategy: str, cash_threshold: float, use_rule_signals: bool = False):
    """執行月度回測"""
    selected_months = get_monthly_periods(start_m, end_m)

    if not selected_months:
        st.error("無效的月份範圍")
        return

    # 如果使用規則信號，先計算
    rule_signals = {}
    if use_rule_signals:
        with st.spinner("計算規則信號中..."):
            start_year = int(start_m.split("-")[0])
            end_year = int(end_m.split("-")[0])
            rule_signals = calculate_monthly_signals(start_year, end_year)
            if not rule_signals:
                st.error("無法計算規則信號，改用手動信號")
                use_rule_signals = False
            else:
                st.success(f"✅ 已計算 {len(rule_signals)} 個月的規則信號")

    # 收集所有需要的股票
    all_symbols = set([benchmark, "SHY"])
    for m in selected_months:
        all_symbols.update(MONTHLY_PORTFOLIOS[m]["holdings"].keys())

    # 取得價格數據
    start_date = f"{start_m}-01"
    # 計算結束日期 (下月第一天)
    end_year, end_month = map(int, end_m.split("-"))
    if end_month == 12:
        end_date = f"{end_year + 1}-01-01"
    else:
        end_date = f"{end_year}-{end_month + 1:02d}-01"

    st.info(f"📅 回測期間: {start_date} ~ {end_date}")

    prices_df = fetch_stock_prices(list(all_symbols), start_date, end_date)

    if prices_df.empty:
        st.error("無法取得股價數據")
        return

    cash_holdings = {"SHY": 1.0}

    # 判斷是否比較模式
    is_compare = strategy == "📊 兩者比較"
    strategies_to_run = ["🛡️ 熊市防禦", "💵 熊市空手"] if is_compare else [strategy]

    # 儲存兩種策略結果
    strategy_results = {}

    for strat in strategies_to_run:
        monthly_results = []
        all_returns = pd.Series(dtype=float)
        bear_months = []

        for m in selected_months:
            m_info = MONTHLY_PORTFOLIOS[m]

            # 根據信號來源決定 signal_score
            if use_rule_signals and m in rule_signals:
                signal_score = rule_signals[m]["score"]
                signal_desc = f"[規則] {', '.join(rule_signals[m]['rules'][:2])}"
            else:
                signal_score = m_info["signal_score"]
                signal_desc = m_info["signal"]

            is_bear = signal_score <= cash_threshold

            if is_bear and m not in bear_months:
                bear_months.append(m)

            # 計算該月日期範圍
            year, month = map(int, m.split("-"))
            m_start = f"{m}-01"
            if month == 12:
                m_end = f"{year + 1}-01-01"
            else:
                m_end = f"{year}-{month + 1:02d}-01"

            mask = (prices_df.index >= m_start) & (prices_df.index < m_end)
            m_prices = prices_df[mask]

            if m_prices.empty:
                continue

            # 根據策略選擇持股
            if is_bear and strat == "💵 熊市空手":
                holdings = cash_holdings
                status = "💵 空手"
            else:
                holdings = m_info["holdings"]
                status = "🔴 防禦" if is_bear else "📈 持股"

            # 計算報酬
            port_returns = calculate_portfolio_returns(m_prices, holdings)
            bench_returns = m_prices[benchmark].pct_change() if benchmark in m_prices.columns else pd.Series()

            p_metrics = calculate_metrics(port_returns)
            b_metrics = calculate_metrics(bench_returns)

            monthly_results.append({
                "月份": m,
                "信號": signal_desc[:25] + "..." if len(signal_desc) > 25 else signal_desc,
                "分數": f"{signal_score:+.1f}",
                "狀態": status,
                "投組": f"{p_metrics['total_return']:.1f}%",
                benchmark: f"{b_metrics.get('total_return', 0):.1f}%",
                "Alpha": f"{p_metrics['total_return'] - b_metrics.get('total_return', 0):+.1f}%",
            })

            all_returns = pd.concat([all_returns, port_returns])

        strategy_results[strat] = {
            "monthly_results": monthly_results,
            "all_returns": all_returns,
            "bear_months": bear_months
        }

    # 取得熊市月份 (兩策略相同)
    bear_months = strategy_results[strategies_to_run[0]]["bear_months"]

    # 顯示熊市月份
    if bear_months:
        st.warning(f"🔴 **熊市月份** (信號 ≤ {cash_threshold}): {', '.join(bear_months)}")

    # 全期基準報酬
    full_bench_returns = prices_df[benchmark].pct_change().dropna() if benchmark in prices_df.columns else pd.Series()
    full_b_metrics = calculate_metrics(full_bench_returns)

    if is_compare:
        # ===== 比較模式 =====
        st.markdown("### 📊 策略比較")

        # 績效對比表
        compare_data = []
        for strat in strategies_to_run:
            all_returns = strategy_results[strat]["all_returns"]
            p_metrics = calculate_metrics(all_returns)
            compare_data.append({
                "策略": strat,
                "總報酬": f"{p_metrics['total_return']:.1f}%",
                "Alpha": f"{p_metrics['total_return'] - full_b_metrics['total_return']:+.1f}%",
                "夏普比率": f"{p_metrics['sharpe']:.2f}",
                "最大回撤": f"{p_metrics['max_drawdown']:.1f}%",
                "波動率": f"{p_metrics['volatility']:.1f}%",
                "勝率": f"{p_metrics['win_rate']:.0f}%",
            })

        st.dataframe(pd.DataFrame(compare_data), use_container_width=True, hide_index=True)

        # 繪製比較圖表
        st.markdown("### 📉 累積報酬走勢比較")
        fig = go.Figure()

        colors = {"🛡️ 熊市防禦": "#2196F3", "💵 熊市空手": "#4CAF50"}
        for strat in strategies_to_run:
            all_returns = strategy_results[strat]["all_returns"]
            if not all_returns.empty:
                port_cum = (1 + all_returns).cumprod()
                fig.add_trace(go.Scatter(
                    x=port_cum.index, y=(port_cum - 1) * 100,
                    name=strat, line=dict(color=colors.get(strat, "#999"), width=2)
                ))

        if not full_bench_returns.empty:
            bench_cum = (1 + full_bench_returns).cumprod()
            fig.add_trace(go.Scatter(
                x=bench_cum.index, y=(bench_cum - 1) * 100,
                name=benchmark, line=dict(color="#FF9800", width=2, dash="dot")
            ))

        # 標記熊市月份
        for m in bear_months:
            year, month = map(int, m.split("-"))
            m_start = f"{m}-01"
            if month == 12:
                m_end = f"{year + 1}-01-01"
            else:
                m_end = f"{year}-{month + 1:02d}-01"
            fig.add_vrect(x0=m_start, x1=m_end, fillcolor="red", opacity=0.1, line_width=0)

        fig.update_layout(
            xaxis_title="日期", yaxis_title="累積報酬 (%)",
            hovermode="x unified", height=400,
            legend=dict(orientation="h", yanchor="bottom", y=1.02)
        )
        st.plotly_chart(fig, use_container_width=True)

        # 月度績效比較
        st.markdown("### 📈 月度績效比較")
        tab1, tab2 = st.tabs(["🛡️ 熊市防禦", "💵 熊市空手"])
        with tab1:
            st.dataframe(pd.DataFrame(strategy_results["🛡️ 熊市防禦"]["monthly_results"]), use_container_width=True, hide_index=True)
        with tab2:
            st.dataframe(pd.DataFrame(strategy_results["💵 熊市空手"]["monthly_results"]), use_container_width=True, hide_index=True)

    else:
        # ===== 單一策略模式 =====
        monthly_results = strategy_results[strategy]["monthly_results"]
        all_returns = strategy_results[strategy]["all_returns"]

        # 顯示結果
        st.markdown("### 📈 月度績效")
        st.dataframe(pd.DataFrame(monthly_results), use_container_width=True, hide_index=True)

        # 全期績效
        full_p_metrics = calculate_metrics(all_returns)

        st.markdown("### 📊 全期績效摘要")
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric("投組總報酬", f"{full_p_metrics['total_return']:.1f}%")
        with col2:
            alpha = full_p_metrics['total_return'] - full_b_metrics['total_return']
            st.metric("Alpha", f"{alpha:+.1f}%")
        with col3:
            st.metric("夏普比率", f"{full_p_metrics['sharpe']:.2f}")
        with col4:
            st.metric("最大回撤", f"{full_p_metrics['max_drawdown']:.1f}%")

        # 繪製圖表
        st.markdown("### 📉 累積報酬走勢")
        fig = go.Figure()

        port_cum = (1 + all_returns).cumprod()
        fig.add_trace(go.Scatter(
            x=port_cum.index, y=(port_cum - 1) * 100,
            name="月度換股", line=dict(color="#2196F3", width=2)
        ))

        if not full_bench_returns.empty:
            bench_cum = (1 + full_bench_returns).cumprod()
            fig.add_trace(go.Scatter(
                x=bench_cum.index, y=(bench_cum - 1) * 100,
                name=benchmark, line=dict(color="#FF9800", width=2)
            ))

        # 標記熊市月份
        for m in bear_months:
            year, month = map(int, m.split("-"))
            m_start = f"{m}-01"
            if month == 12:
                m_end = f"{year + 1}-01-01"
            else:
                m_end = f"{year}-{month + 1:02d}-01"
            fig.add_vrect(x0=m_start, x1=m_end, fillcolor="red", opacity=0.1, line_width=0)

        fig.update_layout(
            xaxis_title="日期", yaxis_title="累積報酬 (%)",
            hovermode="x unified", height=400
        )
        st.plotly_chart(fig, use_container_width=True)

    # 持股變化表
    st.markdown("### 📋 月度持股變化")
    with st.expander("查看詳細持股配置"):
        for m in selected_months:
            m_info = MONTHLY_PORTFOLIOS[m]
            st.markdown(f"**{m}** - {m_info['signal']}")
            holdings_df = pd.DataFrame([
                {"股票": s, "權重": f"{w*100:.0f}%"}
                for s, w in sorted(m_info["holdings"].items(), key=lambda x: x[1], reverse=True)
            ])
            st.dataframe(holdings_df, use_container_width=True, hide_index=True)
            st.markdown("---")


def run_backtest(start_q: str, end_q: str, benchmark: str, strategy: str = "🛡️ 熊市防禦", cash_threshold: float = -0.2):
    """執行回測"""
    quarters = list(QUARTERLY_PORTFOLIOS.keys())
    start_idx = quarters.index(start_q)
    end_idx = quarters.index(end_q)
    selected_quarters = quarters[start_idx:end_idx+1]

    # 收集所有需要的股票
    all_symbols = set([benchmark, "SHY"])  # 加入SHY作為現金替代
    for q in selected_quarters:
        all_symbols.update(QUARTERLY_PORTFOLIOS[q]["holdings"].keys())

    # 取得價格數據
    start_date = QUARTERLY_PORTFOLIOS[start_q]["start"]
    end_date = QUARTERLY_PORTFOLIOS[end_q]["end"]

    st.info(f"📅 回測期間: {start_date} ~ {end_date}")

    prices_df = fetch_stock_prices(list(all_symbols), start_date, end_date)

    if prices_df.empty:
        st.error("無法取得股價數據")
        return

    # 現金配置 (用SHY代替)
    cash_holdings = {"SHY": 1.0}

    def calculate_quarter_returns(q, holdings_override=None):
        """計算單季報酬"""
        q_info = QUARTERLY_PORTFOLIOS[q]
        q_start = q_info["start"]
        q_end = q_info["end"]

        mask = (prices_df.index >= q_start) & (prices_df.index <= q_end)
        q_prices = prices_df[mask]

        if q_prices.empty:
            return None, None, None

        holdings = holdings_override if holdings_override else q_info["holdings"]
        portfolio_returns = calculate_portfolio_returns(q_prices, holdings)
        benchmark_returns = q_prices[benchmark].pct_change() if benchmark in q_prices.columns else pd.Series()

        return portfolio_returns, benchmark_returns, q_prices

    # 計算每季報酬 - 支援多策略
    quarterly_results = []
    all_defensive_returns = pd.Series(dtype=float)  # 熊市防禦策略
    all_cash_returns = pd.Series(dtype=float)       # 熊市空手策略
    bear_quarters = []  # 記錄哪些季度是熊市

    for q in selected_quarters:
        q_info = QUARTERLY_PORTFOLIOS[q]
        q_signal = QUARTER_SIGNALS.get(q, {})
        signal_score = q_signal.get("signal_score", 0)

        is_bear = signal_score <= cash_threshold
        if is_bear:
            bear_quarters.append(q)

        # 計算防禦策略報酬 (原始配置)
        def_returns, bench_returns, q_prices = calculate_quarter_returns(q)
        if def_returns is None:
            st.warning(f"{q} 無數據，跳過")
            continue

        # 計算空手策略報酬 (熊市時100%現金)
        if is_bear:
            cash_returns, _, _ = calculate_quarter_returns(q, cash_holdings)
        else:
            cash_returns = def_returns.copy()

        # 計算績效
        def_metrics = calculate_metrics(def_returns)
        cash_metrics = calculate_metrics(cash_returns) if cash_returns is not None else {}
        bench_metrics = calculate_metrics(bench_returns) if bench_returns is not None else {}

        # 根據策略選擇顯示內容
        if strategy == "📊 兩者比較":
            quarterly_results.append({
                "季度": q,
                "信號": q_info["name"],
                "分數": f"{signal_score:+.1f}",
                "狀態": "🔴 熊市" if is_bear else "🟢 正常",
                "防禦策略": f"{def_metrics['total_return']:.1f}%",
                "空手策略": f"{cash_metrics.get('total_return', 0):.1f}%",
                f"{benchmark}": f"{bench_metrics.get('total_return', 0):.1f}%",
                "防禦Alpha": f"{def_metrics['total_return'] - bench_metrics.get('total_return', 0):+.1f}%",
                "空手Alpha": f"{cash_metrics.get('total_return', 0) - bench_metrics.get('total_return', 0):+.1f}%",
            })
        else:
            # 單一策略
            if strategy == "💵 熊市空手":
                p_metrics = cash_metrics
                p_returns = cash_returns
            else:
                p_metrics = def_metrics
                p_returns = def_returns

            quarterly_results.append({
                "季度": q,
                "決策信號": q_info["name"],
                "信號分數": f"{signal_score:+.1f}",
                "狀態": "🔴 空手" if (is_bear and strategy == "💵 熊市空手") else ("🛡️ 防禦" if is_bear else "📈 持股"),
                "投組報酬": f"{p_metrics['total_return']:.1f}%",
                f"{benchmark}": f"{bench_metrics.get('total_return', 0):.1f}%",
                "Alpha": f"{p_metrics['total_return'] - bench_metrics.get('total_return', 0):+.1f}%",
                "最大回撤": f"{p_metrics['max_drawdown']:.1f}%",
            })

        # 累積報酬
        all_defensive_returns = pd.concat([all_defensive_returns, def_returns])
        all_cash_returns = pd.concat([all_cash_returns, cash_returns])

    # 顯示熊市季度
    if bear_quarters:
        st.warning(f"🔴 **熊市季度** (信號 ≤ {cash_threshold}): {', '.join(bear_quarters)}")

    # 顯示季度結果
    st.header("📈 季度績效比較")
    st.dataframe(pd.DataFrame(quarterly_results), use_container_width=True, hide_index=True)

    # 計算全期績效
    st.divider()
    st.header("📊 全期績效摘要")

    # 取得全期基準報酬
    full_benchmark_returns = prices_df[benchmark].pct_change().dropna() if benchmark in prices_df.columns else pd.Series()

    full_def_metrics = calculate_metrics(all_defensive_returns)
    full_cash_metrics = calculate_metrics(all_cash_returns)
    full_b_metrics = calculate_metrics(full_benchmark_returns)

    if strategy == "📊 兩者比較":
        # 比較模式 - 顯示兩種策略
        st.markdown("### 策略績效比較")

        comparison_data = {
            "指標": ["總報酬", "年化報酬", "夏普比率", "波動率", "最大回撤", "勝率"],
            "🛡️ 熊市防禦": [
                f"{full_def_metrics['total_return']:.1f}%",
                f"{full_def_metrics['annualized_return']:.1f}%",
                f"{full_def_metrics['sharpe']:.2f}",
                f"{full_def_metrics['volatility']:.1f}%",
                f"{full_def_metrics['max_drawdown']:.1f}%",
                f"{full_def_metrics['win_rate']:.1f}%",
            ],
            "💵 熊市空手": [
                f"{full_cash_metrics['total_return']:.1f}%",
                f"{full_cash_metrics['annualized_return']:.1f}%",
                f"{full_cash_metrics['sharpe']:.2f}",
                f"{full_cash_metrics['volatility']:.1f}%",
                f"{full_cash_metrics['max_drawdown']:.1f}%",
                f"{full_cash_metrics['win_rate']:.1f}%",
            ],
            f"{benchmark}": [
                f"{full_b_metrics['total_return']:.1f}%",
                f"{full_b_metrics['annualized_return']:.1f}%",
                f"{full_b_metrics['sharpe']:.2f}",
                f"{full_b_metrics['volatility']:.1f}%",
                f"{full_b_metrics['max_drawdown']:.1f}%",
                f"{full_b_metrics['win_rate']:.1f}%",
            ],
        }
        st.dataframe(pd.DataFrame(comparison_data), use_container_width=True, hide_index=True)

        # 勝者判定
        def_alpha = full_def_metrics['total_return'] - full_b_metrics['total_return']
        cash_alpha = full_cash_metrics['total_return'] - full_b_metrics['total_return']

        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("🛡️ 防禦 Alpha", f"{def_alpha:+.1f}%")
        with col2:
            st.metric("💵 空手 Alpha", f"{cash_alpha:+.1f}%")
        with col3:
            winner = "💵 熊市空手" if cash_alpha > def_alpha else "🛡️ 熊市防禦"
            diff = abs(cash_alpha - def_alpha)
            st.metric("勝者", winner, delta=f"+{diff:.1f}%")

    else:
        # 單一策略模式
        if strategy == "💵 熊市空手":
            full_p_metrics = full_cash_metrics
        else:
            full_p_metrics = full_def_metrics

        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric("投組總報酬", f"{full_p_metrics['total_return']:.1f}%")
            st.metric(f"{benchmark}總報酬", f"{full_b_metrics['total_return']:.1f}%")
        with col2:
            alpha = full_p_metrics['total_return'] - full_b_metrics['total_return']
            st.metric("超額報酬 (Alpha)", f"{alpha:.1f}%",
                      delta=f"{'勝' if alpha > 0 else '負'}")
            st.metric("年化報酬", f"{full_p_metrics['annualized_return']:.1f}%")
        with col3:
            st.metric("夏普比率", f"{full_p_metrics['sharpe']:.2f}")
            st.metric("波動率", f"{full_p_metrics['volatility']:.1f}%")
        with col4:
            st.metric("最大回撤", f"{full_p_metrics['max_drawdown']:.1f}%")
            st.metric("勝率", f"{full_p_metrics['win_rate']:.1f}%")

    # 繪製累積報酬圖
    st.divider()
    st.header("📉 累積報酬走勢")

    fig = go.Figure()

    # 防禦策略累積
    if strategy in ["🛡️ 熊市防禦", "📊 兩者比較"]:
        defensive_cum = (1 + all_defensive_returns).cumprod()
        fig.add_trace(go.Scatter(
            x=defensive_cum.index,
            y=(defensive_cum - 1) * 100,
            name="🛡️ 熊市防禦",
            line=dict(color="#2196F3", width=2),
        ))

    # 空手策略累積
    if strategy in ["💵 熊市空手", "📊 兩者比較"]:
        cash_cum = (1 + all_cash_returns).cumprod()
        fig.add_trace(go.Scatter(
            x=cash_cum.index,
            y=(cash_cum - 1) * 100,
            name="💵 熊市空手",
            line=dict(color="#4CAF50", width=2),
        ))

    # 基準累積
    if not full_benchmark_returns.empty:
        benchmark_cum = (1 + full_benchmark_returns).cumprod()
        fig.add_trace(go.Scatter(
            x=benchmark_cum.index,
            y=(benchmark_cum - 1) * 100,
            name=f"{benchmark}",
            line=dict(color="#FF9800", width=2),
        ))

    # 標記熊市季度
    for q in bear_quarters:
        q_start = QUARTERLY_PORTFOLIOS[q]["start"]
        q_end = QUARTERLY_PORTFOLIOS[q]["end"]
        fig.add_vrect(x0=q_start, x1=q_end, fillcolor="red", opacity=0.1, line_width=0)

    # 標記季度分界
    for q in selected_quarters:
        q_start = QUARTERLY_PORTFOLIOS[q]["start"]
        fig.add_vline(x=q_start, line_dash="dash", line_color="gray", opacity=0.5)
        fig.add_annotation(x=q_start, y=1.05, yref="paper", text=q, showarrow=False, font=dict(size=10))

    fig.update_layout(
        title="累積報酬率 (%)",
        xaxis_title="日期",
        yaxis_title="累積報酬 (%)",
        hovermode="x unified",
        height=500,
    )
    st.plotly_chart(fig, use_container_width=True)

    # 顯示持股配置與決策信號
    st.divider()
    st.header("📋 各季決策信號與持股配置")

    for q in selected_quarters:
        q_info = QUARTERLY_PORTFOLIOS[q]
        q_signal = QUARTER_SIGNALS.get(q, {})
        signal_score = q_signal.get("signal_score", 0)

        # 根據信號分數決定顏色
        if signal_score >= 0.5:
            signal_color = "🟢"
        elif signal_score >= 0.2:
            signal_color = "🟡"
        elif signal_score >= -0.2:
            signal_color = "⚪"
        elif signal_score >= -0.5:
            signal_color = "🟠"
        else:
            signal_color = "🔴"

        with st.expander(f"{signal_color} **{q}** - {q_info['name']} (信號: {signal_score:+.1f})"):
            # 顯示季初可得信號
            st.markdown("##### 📡 季初決策信號 (季初第一天可得資訊)")
            signal_text = q_info.get("signal", "")
            st.info(f"**{signal_text}**")

            if q_signal:
                col1, col2, col3 = st.columns(3)
                with col1:
                    st.markdown(f"**Fed態度**: {q_signal.get('fed_stance', '—')}")
                    st.markdown(f"**殖利率曲線**: {q_signal.get('yield_curve', '—')}")
                with col2:
                    st.markdown(f"**CPI趨勢**: {q_signal.get('cpi_trend', '—')}")
                    st.markdown(f"**SPY vs 200MA**: {q_signal.get('spy_vs_200ma', '—')}")
                with col3:
                    st.markdown(f"**VIX**: {q_signal.get('vix', '—')}")
                    if q_signal.get('ai_momentum'):
                        st.markdown(f"**AI動能**: {q_signal.get('ai_momentum')}")

            st.markdown("##### 💼 持股配置")
            st.markdown(f"**期間**: {q_info['start']} ~ {q_info['end']}")

            holdings_df = pd.DataFrame([
                {"股票": s, "權重": f"{w*100:.0f}%", "公司": STOCK_DETAILS.get(s, {}).get("name", s)}
                for s, w in sorted(q_info["holdings"].items(), key=lambda x: x[1], reverse=True)
            ])
            st.dataframe(holdings_df, use_container_width=True, hide_index=True)

    # 風險提示
    st.divider()
    st.warning("""
    ⚠️ **免責聲明**

    此回測結果僅供參考，不構成投資建議。
    - 過去績效不代表未來表現
    - 回測未考慮交易成本、滑點、稅費
    - 實際執行可能有流動性問題
    - 請謹慎評估自身風險承受度
    """)


# ========== 個股深度分析頁面 ==========
FOCUS_STOCKS = {
    "AAPL": {"name": "Apple", "sector": "科技", "description": "消費電子與服務巨頭，iPhone、Mac、服務生態系"},
    "INTC": {"name": "Intel", "sector": "半導體", "description": "CPU 製造商，正在轉型晶圓代工"},
    "PLTR": {"name": "Palantir", "sector": "軟體/AI", "description": "大數據分析平台，政府與企業 AI 解決方案"},
    "LITE": {"name": "Lumentum", "sector": "光學/光子", "description": "光學與光子產品，3D 感測、光通訊"},
}


def render_individual_stock_page(selected_date: date):
    """渲染個股深度分析頁面"""
    st.title("🔬 個股深度分析")

    # 股票選擇
    col1, col2 = st.columns([1, 3])
    with col1:
        selected_symbol = st.selectbox(
            "選擇股票",
            list(FOCUS_STOCKS.keys()),
            format_func=lambda x: f"{x} - {FOCUS_STOCKS[x]['name']}"
        )

    stock_info = FOCUS_STOCKS[selected_symbol]

    with col2:
        st.markdown(f"""
        ### {selected_symbol} - {stock_info['name']}
        **產業**: {stock_info['sector']} | {stock_info['description']}
        """)

    st.divider()

    # ========== 取得股價數據 ==========
    end_date = date.today()
    start_date = end_date - timedelta(days=365)

    # 使用 yfinance 取得最新數據
    try:
        import yfinance as yf
        ticker = yf.Ticker(selected_symbol)
        df = ticker.history(start=start_date, end=end_date)
        df = df.reset_index()
        df.columns = [c.lower() for c in df.columns]

        if df.empty:
            st.warning(f"無法取得 {selected_symbol} 的股價數據")
            return

        # 計算技術指標
        df['ma20'] = df['close'].rolling(window=20).mean()
        df['ma50'] = df['close'].rolling(window=50).mean()
        df['ma200'] = df['close'].rolling(window=200).mean()

        # RSI
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        df['rsi'] = 100 - (100 / (1 + rs))

        # MACD
        exp1 = df['close'].ewm(span=12, adjust=False).mean()
        exp2 = df['close'].ewm(span=26, adjust=False).mean()
        df['macd'] = exp1 - exp2
        df['signal'] = df['macd'].ewm(span=9, adjust=False).mean()
        df['macd_hist'] = df['macd'] - df['signal']

        # 布林通道
        df['bb_mid'] = df['close'].rolling(window=20).mean()
        df['bb_std'] = df['close'].rolling(window=20).std()
        df['bb_upper'] = df['bb_mid'] + 2 * df['bb_std']
        df['bb_lower'] = df['bb_mid'] - 2 * df['bb_std']

    except Exception as e:
        st.error(f"取得股價數據時發生錯誤: {e}")
        return

    # ========== 關鍵指標卡片 ==========
    latest = df.iloc[-1]
    prev = df.iloc[-2] if len(df) > 1 else latest

    price_change = latest['close'] - prev['close']
    price_change_pct = (price_change / prev['close']) * 100

    col1, col2, col3, col4, col5 = st.columns(5)
    with col1:
        st.metric(
            "收盤價",
            f"${latest['close']:.2f}",
            f"{price_change:+.2f} ({price_change_pct:+.2f}%)"
        )
    with col2:
        st.metric("成交量", f"{latest['volume']/1e6:.1f}M")
    with col3:
        rsi_val = latest['rsi']
        rsi_status = "超買" if rsi_val > 70 else ("超賣" if rsi_val < 30 else "中性")
        st.metric("RSI (14)", f"{rsi_val:.1f}", rsi_status)
    with col4:
        macd_val = latest['macd']
        macd_signal = "多頭" if macd_val > latest['signal'] else "空頭"
        st.metric("MACD", f"{macd_val:.2f}", macd_signal)
    with col5:
        # 計算距離 52 週高低
        high_52w = df['high'].tail(252).max()
        low_52w = df['low'].tail(252).min()
        pct_from_high = ((latest['close'] - high_52w) / high_52w) * 100
        st.metric("距52週高", f"{pct_from_high:.1f}%", f"${high_52w:.2f}")

    st.divider()

    # ========== 圖表區域 ==========
    tab1, tab2, tab3, tab4 = st.tabs(["📈 價格走勢", "📊 技術指標", "📰 相關新聞", "🎯 分析總結"])

    with tab1:
        # K線圖 + 均線
        fig = make_subplots(
            rows=2, cols=1,
            shared_xaxes=True,
            vertical_spacing=0.05,
            row_heights=[0.7, 0.3],
            subplot_titles=("價格與均線", "成交量")
        )

        # K線
        fig.add_trace(go.Candlestick(
            x=df['date'],
            open=df['open'],
            high=df['high'],
            low=df['low'],
            close=df['close'],
            name='K線'
        ), row=1, col=1)

        # 均線
        fig.add_trace(go.Scatter(x=df['date'], y=df['ma20'], name='MA20', line=dict(color='orange', width=1)), row=1, col=1)
        fig.add_trace(go.Scatter(x=df['date'], y=df['ma50'], name='MA50', line=dict(color='blue', width=1)), row=1, col=1)
        fig.add_trace(go.Scatter(x=df['date'], y=df['ma200'], name='MA200', line=dict(color='red', width=1)), row=1, col=1)

        # 布林通道
        fig.add_trace(go.Scatter(x=df['date'], y=df['bb_upper'], name='BB Upper', line=dict(color='gray', dash='dash', width=0.5)), row=1, col=1)
        fig.add_trace(go.Scatter(x=df['date'], y=df['bb_lower'], name='BB Lower', line=dict(color='gray', dash='dash', width=0.5), fill='tonexty', fillcolor='rgba(128,128,128,0.1)'), row=1, col=1)

        # 成交量
        colors = ['red' if row['close'] < row['open'] else 'green' for _, row in df.iterrows()]
        fig.add_trace(go.Bar(x=df['date'], y=df['volume'], name='成交量', marker_color=colors), row=2, col=1)

        fig.update_layout(
            height=600,
            xaxis_rangeslider_visible=False,
            showlegend=True,
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
        )
        st.plotly_chart(fig, use_container_width=True)

    with tab2:
        # RSI + MACD 圖
        fig2 = make_subplots(
            rows=2, cols=1,
            shared_xaxes=True,
            vertical_spacing=0.1,
            subplot_titles=("RSI (14)", "MACD")
        )

        # RSI
        fig2.add_trace(go.Scatter(x=df['date'], y=df['rsi'], name='RSI', line=dict(color='purple')), row=1, col=1)
        fig2.add_hline(y=70, line_dash="dash", line_color="red", row=1, col=1)
        fig2.add_hline(y=30, line_dash="dash", line_color="green", row=1, col=1)

        # MACD
        fig2.add_trace(go.Scatter(x=df['date'], y=df['macd'], name='MACD', line=dict(color='blue')), row=2, col=1)
        fig2.add_trace(go.Scatter(x=df['date'], y=df['signal'], name='Signal', line=dict(color='orange')), row=2, col=1)
        colors_macd = ['green' if v > 0 else 'red' for v in df['macd_hist']]
        fig2.add_trace(go.Bar(x=df['date'], y=df['macd_hist'], name='Histogram', marker_color=colors_macd), row=2, col=1)

        fig2.update_layout(height=500, showlegend=True)
        st.plotly_chart(fig2, use_container_width=True)

    with tab3:
        # 相關新聞
        st.markdown("#### 📰 近期相關新聞")

        # 搜尋關鍵字
        search_keywords = {
            "AAPL": ["apple", "iphone", "aapl", "tim cook"],
            "INTC": ["intel", "intc", "pat gelsinger", "foundry"],
            "PLTR": ["palantir", "pltr", "alex karp"],
            "LITE": ["lumentum", "lite", "optical", "photonics"],
        }

        keywords = search_keywords.get(selected_symbol, [selected_symbol.lower()])

        # 取得相關新聞 - 使用統一資料層
        related_news = []
        try:
            client = _get_data_client()
            for kw in keywords[:2]:  # 限制關鍵字數量
                results = client.search_news(kw, limit=10)
                related_news.extend(results)
        except Exception as e:
            pass

        # 去重
        seen_titles = set()
        unique_news = []
        for n in related_news:
            if n["title"] not in seen_titles:
                seen_titles.add(n["title"])
                unique_news.append(n)

        if unique_news:
            for news in unique_news[:15]:
                pub_date = news.get("published_at", "")[:10] if news.get("published_at") else ""
                source = news.get("source", "")
                st.markdown(f"**{pub_date}** | {source}")
                st.markdown(f"[{news['title']}]({news.get('url', '#')})")
                st.markdown("---")
        else:
            st.info("暫無相關新聞")

    with tab4:
        # 技術分析總結
        st.markdown("#### 🎯 技術分析總結")

        # 趨勢判斷
        trend_signals = []
        if latest['close'] > latest['ma20']:
            trend_signals.append("✅ 股價在 MA20 之上 (短期多頭)")
        else:
            trend_signals.append("❌ 股價在 MA20 之下 (短期空頭)")

        if latest['close'] > latest['ma50']:
            trend_signals.append("✅ 股價在 MA50 之上 (中期多頭)")
        else:
            trend_signals.append("❌ 股價在 MA50 之下 (中期空頭)")

        if latest['close'] > latest['ma200']:
            trend_signals.append("✅ 股價在 MA200 之上 (長期多頭)")
        else:
            trend_signals.append("❌ 股價在 MA200 之下 (長期空頭)")

        if latest['ma20'] > latest['ma50']:
            trend_signals.append("✅ MA20 > MA50 (黃金交叉形態)")
        else:
            trend_signals.append("⚠️ MA20 < MA50 (死亡交叉形態)")

        # RSI 判斷
        if rsi_val > 70:
            trend_signals.append("⚠️ RSI > 70 (超買區，可能回調)")
        elif rsi_val < 30:
            trend_signals.append("🟢 RSI < 30 (超賣區，可能反彈)")
        else:
            trend_signals.append("⚪ RSI 在中性區間")

        # MACD 判斷
        if latest['macd'] > latest['signal']:
            trend_signals.append("✅ MACD 在信號線之上 (多頭動能)")
        else:
            trend_signals.append("❌ MACD 在信號線之下 (空頭動能)")

        # 布林通道判斷
        if latest['close'] > latest['bb_upper']:
            trend_signals.append("⚠️ 股價突破布林上軌 (可能超漲)")
        elif latest['close'] < latest['bb_lower']:
            trend_signals.append("🟢 股價跌破布林下軌 (可能超跌)")

        col1, col2 = st.columns(2)
        with col1:
            st.markdown("**趨勢信號**")
            for signal in trend_signals:
                st.markdown(signal)

        with col2:
            # 綜合評分
            bullish_count = sum(1 for s in trend_signals if s.startswith("✅") or s.startswith("🟢"))
            bearish_count = sum(1 for s in trend_signals if s.startswith("❌") or s.startswith("⚠️"))
            total = bullish_count + bearish_count

            if total > 0:
                score = (bullish_count / total) * 100

                st.markdown("**綜合評分**")
                if score >= 70:
                    st.success(f"🟢 偏多 ({score:.0f}分)")
                    st.markdown("技術面偏多頭，但仍需注意風險管理")
                elif score >= 40:
                    st.warning(f"🟡 中性 ({score:.0f}分)")
                    st.markdown("多空交戰，建議觀望或輕倉")
                else:
                    st.error(f"🔴 偏空 ({score:.0f}分)")
                    st.markdown("技術面偏空頭，建議謹慎操作")

            # 支撐壓力
            st.markdown("**關鍵價位**")
            st.markdown(f"- 支撐: ${latest['bb_lower']:.2f} (布林下軌)")
            st.markdown(f"- 壓力: ${latest['bb_upper']:.2f} (布林上軌)")
            st.markdown(f"- 52週高: ${high_52w:.2f}")
            st.markdown(f"- 52週低: ${low_52w:.2f}")


def render_stock_page(selected_date: date):
    """渲染股票數據頁面"""
    st.title("📈 股票數據與新聞")

    # 檢查金融資料庫是否存在
    if not FINANCE_DB_PATH.exists():
        st.error("找不到金融資料庫 (finance.db)")
        st.info("請執行 `python finance_collector.py --init --fast` 來初始化")
        return

    # 取得追蹤清單
    watchlist = get_watchlist()
    if not watchlist:
        st.warning("追蹤清單為空，請先初始化")
        return

    # 依市場分組
    markets = {}
    for stock in watchlist:
        market = stock["market"]
        if market not in markets:
            markets[market] = []
        markets[market].append(stock)

    # ========== 側邊選擇 ==========
    col1, col2 = st.columns([1, 3])

    with col1:
        # 市場選擇
        market_options = list(markets.keys())
        selected_market = st.selectbox("選擇市場", market_options, index=0)

        # 依產業分組
        stocks_in_market = markets[selected_market]

        # 產業篩選
        sectors = list(set(s.get("sector") or "未分類" for s in stocks_in_market))
        sectors.sort()
        sectors.insert(0, "全部")
        selected_sector = st.selectbox("產業篩選", sectors, index=0)

        # 篩選股票
        if selected_sector != "全部":
            filtered_stocks = [s for s in stocks_in_market if (s.get("sector") or "未分類") == selected_sector]
        else:
            filtered_stocks = stocks_in_market

        # 股票選擇
        stock_options = {f"{s['symbol']} - {s['name'][:15] if s['name'] else s['symbol']}": s["symbol"] for s in filtered_stocks}
        selected_stock_label = st.selectbox("選擇股票", list(stock_options.keys()))
        selected_symbol = stock_options[selected_stock_label]

        # 顯示股票資訊卡
        stock_info = get_stock_info(selected_symbol)
        if stock_info:
            st.markdown("---")
            st.markdown(f"**{stock_info['name']}**")
            if stock_info.get('sector'):
                st.markdown(f"🏷️ {stock_info['sector']}")
            if stock_info.get('industry'):
                st.markdown(f"🏭 {stock_info['industry']}")
            if stock_info.get('description'):
                st.caption(stock_info['description'])

        st.markdown("---")

        # 時間範圍
        period_options = {
            "1個月": 30,
            "3個月": 90,
            "6個月": 180,
            "1年": 365,
        }
        selected_period = st.selectbox("時間範圍", list(period_options.keys()), index=1)
        days = period_options[selected_period]

        end_date = selected_date
        start_date = end_date - timedelta(days=days)

    # ========== 主要內容 ==========
    with col2:
        # 取得價格數據
        df = get_stock_prices(selected_symbol, start_date, end_date)

        if df.empty:
            st.warning(f"沒有 {selected_symbol} 的價格數據")
            return

        # 取得相關新聞數量
        news_counts = get_news_in_date_range(start_date, end_date)

        # 建立圖表
        fig = make_subplots(
            rows=3, cols=1,
            shared_xaxes=True,
            vertical_spacing=0.05,
            row_heights=[0.5, 0.25, 0.25],
            subplot_titles=(f"{selected_symbol} 價格走勢", "成交量", "相關新聞數量")
        )

        # 價格 K 線圖
        fig.add_trace(
            go.Candlestick(
                x=df["date"],
                open=df["open"],
                high=df["high"],
                low=df["low"],
                close=df["close"],
                name="價格",
                increasing_line_color="#26a69a",
                decreasing_line_color="#ef5350"
            ),
            row=1, col=1
        )

        # 加入均線
        if len(df) >= 5:
            df["MA5"] = df["close"].rolling(window=5).mean()
            fig.add_trace(
                go.Scatter(x=df["date"], y=df["MA5"], name="MA5",
                           line=dict(color="orange", width=1)),
                row=1, col=1
            )

        if len(df) >= 20:
            df["MA20"] = df["close"].rolling(window=20).mean()
            fig.add_trace(
                go.Scatter(x=df["date"], y=df["MA20"], name="MA20",
                           line=dict(color="blue", width=1)),
                row=1, col=1
            )

        # 成交量
        colors = ["#26a69a" if row["close"] >= row["open"] else "#ef5350"
                  for _, row in df.iterrows()]
        fig.add_trace(
            go.Bar(x=df["date"], y=df["volume"], name="成交量",
                   marker_color=colors, showlegend=False),
            row=2, col=1
        )

        # 新聞數量
        news_dates = []
        news_values = []
        for d, count in sorted(news_counts.items()):
            news_dates.append(d)
            news_values.append(count)

        fig.add_trace(
            go.Bar(x=news_dates, y=news_values, name="新聞數",
                   marker_color="#2196f3", showlegend=False),
            row=3, col=1
        )

        # 標記選擇的日期
        fig.add_shape(
            type="line",
            x0=selected_date.strftime("%Y-%m-%d"),
            x1=selected_date.strftime("%Y-%m-%d"),
            y0=0,
            y1=1,
            yref="paper",
            line=dict(color="red", width=1, dash="dash"),
        )
        fig.add_annotation(
            x=selected_date.strftime("%Y-%m-%d"),
            y=1.02,
            yref="paper",
            text=f"選擇日期",
            showarrow=False,
            font=dict(color="red", size=10)
        )

        # 更新版面
        fig.update_layout(
            height=700,
            xaxis_rangeslider_visible=False,
            showlegend=True,
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
            margin=dict(l=50, r=50, t=80, b=50)
        )

        fig.update_xaxes(
            rangebreaks=[dict(bounds=["sat", "mon"])],  # 隱藏週末
        )

        st.plotly_chart(fig, use_container_width=True)

    # ========== 股票資訊與新聞 ==========
    st.divider()

    col_info, col_news = st.columns([1, 2])

    with col_info:
        st.subheader("📊 基本面數據")

        # 取得最新價格
        latest = df.iloc[-1] if not df.empty else None

        if latest is not None:
            prev_close = df.iloc[-2]["close"] if len(df) > 1 else latest["close"]
            change = latest["close"] - prev_close
            change_pct = (change / prev_close) * 100

            if change >= 0:
                st.metric("最新收盤價", f"${latest['close']:.2f}",
                          f"+{change:.2f} (+{change_pct:.2f}%)")
            else:
                st.metric("最新收盤價", f"${latest['close']:.2f}",
                          f"{change:.2f} ({change_pct:.2f}%)")

            col_a, col_b = st.columns(2)
            with col_a:
                st.metric("最高", f"${latest['high']:.2f}")
            with col_b:
                st.metric("最低", f"${latest['low']:.2f}")

            st.metric("成交量", f"{latest['volume']:,.0f}")

        # 基本面數據
        fundamentals = get_stock_fundamentals(selected_symbol)
        if fundamentals:
            st.markdown("---")
            st.markdown("**估值指標**")

            fund_metrics = [
                ("本益比 (P/E)", fundamentals.get("pe_ratio")),
                ("股價淨值比 (P/B)", fundamentals.get("pb_ratio")),
                ("殖利率", fundamentals.get("dividend_yield")),
                ("Beta", fundamentals.get("beta")),
            ]

            for label, value in fund_metrics:
                if value is not None:
                    if "殖利率" in label:
                        st.markdown(f"• {label}: {value*100:.2f}%")
                    else:
                        st.markdown(f"• {label}: {value:.2f}")

            if fundamentals.get("market_cap"):
                market_cap = fundamentals["market_cap"]
                if market_cap >= 1e12:
                    st.markdown(f"• 市值: ${market_cap/1e12:.2f}T")
                elif market_cap >= 1e9:
                    st.markdown(f"• 市值: ${market_cap/1e9:.2f}B")
                else:
                    st.markdown(f"• 市值: ${market_cap/1e6:.2f}M")

        # 交易建議
        st.markdown("---")
        st.subheader("🎯 交易建議")

        try:
            analyzer = TechnicalAnalyzer(str(FINANCE_DB_PATH))
            analysis = analyzer.get_current_analysis(selected_symbol)

            rec = analysis['recommendation']
            rec_text = analysis.get('recommendation_text', rec)
            confidence = analysis['confidence']

            if rec in ['STRONG_BUY', 'BUY']:
                st.success(f"**{rec_text}** (信心度: {confidence:.0f}%)")
            elif rec in ['STRONG_SELL', 'SELL']:
                st.error(f"**{rec_text}** (信心度: {confidence:.0f}%)")
            else:
                st.warning(f"**{rec_text}** (信心度: {confidence:.0f}%)")

            # 簡要理由
            reasons = analysis.get('reasons', [])
            if reasons:
                with st.expander("📋 分析理由"):
                    for reason in reasons[:3]:
                        st.markdown(f"• {reason}")
        except Exception as e:
            st.info("無法取得交易建議")

    with col_news:
        st.subheader(f"📰 {selected_date} 相關新聞")

        # 取得相關新聞
        related_news = get_news_for_stock(selected_symbol, selected_date)

        if related_news:
            st.markdown(f"找到 **{len(related_news)}** 則相關新聞")
            st.divider()

            for news in related_news[:10]:
                # 情緒分析
                text = (news["title"] + " " + (news["content"] or "")).lower()
                sentiment = "🟡"
                for kw in POSITIVE_KEYWORDS:
                    if kw in text:
                        sentiment = "🟢"
                        break
                for kw in NEGATIVE_KEYWORDS:
                    if kw in text:
                        sentiment = "🔴" if sentiment == "🟡" else "🟡"
                        break

                with st.expander(f"{sentiment} {news['title'][:70]}...", expanded=False):
                    st.markdown(f"**來源**: {news['source']}")
                    if news["published_at"]:
                        st.markdown(f"**時間**: {news['published_at']}")
                    if news["content"]:
                        st.write(news["content"][:300] + "..." if len(news["content"]) > 300 else news["content"])
                    if news["url"]:
                        st.link_button("🔗 閱讀原文", news["url"])
        else:
            st.info(f"沒有找到與 {selected_symbol} 相關的新聞")
            st.markdown("**可能原因:**")
            st.markdown("- 該日期沒有收集新聞")
            st.markdown("- 新聞標題/內容中沒有提及該股票")

    # ========== 多股票比較 ==========
    st.divider()
    st.subheader("📊 多股票比較")

    # 選擇比較的股票
    all_symbols = [s["symbol"] for s in watchlist]
    compare_symbols = st.multiselect(
        "選擇要比較的股票 (最多5檔)",
        all_symbols,
        default=[selected_symbol],
        max_selections=5
    )

    if len(compare_symbols) >= 2:
        # 取得所有股票的數據
        compare_data = {}
        for sym in compare_symbols:
            sym_df = get_stock_prices(sym, start_date, end_date)
            if not sym_df.empty:
                # 計算報酬率
                first_price = sym_df.iloc[0]["close"]
                sym_df["return"] = (sym_df["close"] / first_price - 1) * 100
                compare_data[sym] = sym_df

        if compare_data:
            # 繪製比較圖
            fig_compare = go.Figure()

            for sym, sym_df in compare_data.items():
                fig_compare.add_trace(
                    go.Scatter(
                        x=sym_df["date"],
                        y=sym_df["return"],
                        name=sym,
                        mode="lines"
                    )
                )

            fig_compare.update_layout(
                title="累積報酬率比較 (%)",
                height=400,
                xaxis_title="日期",
                yaxis_title="報酬率 (%)",
                hovermode="x unified"
            )

            fig_compare.add_hline(y=0, line_dash="dash", line_color="gray")

            st.plotly_chart(fig_compare, use_container_width=True)

            # 統計表格
            stats_data = []
            for sym, sym_df in compare_data.items():
                returns = sym_df["close"].pct_change().dropna()
                stats_data.append({
                    "股票": sym,
                    "起始價": f"${sym_df.iloc[0]['close']:.2f}",
                    "最新價": f"${sym_df.iloc[-1]['close']:.2f}",
                    "累積報酬": f"{sym_df.iloc[-1]['return']:.2f}%",
                    "日均報酬": f"{returns.mean()*100:.3f}%",
                    "波動率": f"{returns.std()*100:.2f}%",
                })

            st.dataframe(pd.DataFrame(stats_data), use_container_width=True, hide_index=True)
    else:
        st.info("請選擇至少 2 檔股票來進行比較")


def render_analysis_page():
    """渲染股票分析頁面"""
    st.title("🎯 交易策略分析")

    # 檢查金融資料庫是否存在
    if not FINANCE_DB_PATH.exists():
        st.error("找不到金融資料庫 (finance.db)")
        return

    analyzer = TechnicalAnalyzer(str(FINANCE_DB_PATH))

    # 分析模式選擇
    analysis_mode = st.radio(
        "分析模式",
        ["📊 個股分析", "🏆 買賣排行榜", "📈 策略回測"],
        horizontal=True
    )

    st.divider()

    if analysis_mode == "📊 個股分析":
        render_single_stock_analysis(analyzer)
    elif analysis_mode == "🏆 買賣排行榜":
        render_top_picks(analyzer)
    else:
        render_strategy_backtest(analyzer)


def render_single_stock_analysis(analyzer: TechnicalAnalyzer):
    """個股分析"""
    # 取得股票清單
    watchlist = get_watchlist()
    if not watchlist:
        st.warning("追蹤清單為空")
        return

    # 股票選擇
    col1, col2 = st.columns([1, 2])

    with col1:
        stock_options = {f"{s['symbol']} - {s['name'][:15] if s['name'] else s['symbol']}": s["symbol"] for s in watchlist}
        selected_stock_label = st.selectbox("選擇股票", list(stock_options.keys()), key="analysis_stock")
        selected_symbol = stock_options[selected_stock_label]

        # 執行分析按鈕
        analyze_btn = st.button("🔍 執行分析", type="primary", use_container_width=True)

    if analyze_btn or 'last_analysis' in st.session_state:
        with st.spinner("分析中..."):
            analysis = analyzer.get_current_analysis(selected_symbol)
            st.session_state['last_analysis'] = analysis

        # 顯示建議
        with col2:
            # 建議卡片
            rec = analysis['recommendation']
            rec_text = analysis.get('recommendation_text', rec)
            confidence = analysis['confidence']

            if rec in ['STRONG_BUY', 'BUY']:
                rec_color = "green"
                rec_icon = "🟢"
            elif rec in ['STRONG_SELL', 'SELL']:
                rec_color = "red"
                rec_icon = "🔴"
            else:
                rec_color = "orange"
                rec_icon = "🟡"

            st.markdown(f"""
            <div style="background-color: {'#e8f5e9' if rec_color == 'green' else '#ffebee' if rec_color == 'red' else '#fff3e0'};
                        padding: 20px; border-radius: 10px; text-align: center;">
                <h2 style="color: {rec_color}; margin: 0;">{rec_icon} {rec_text}</h2>
                <p style="margin: 10px 0;">信心度: <strong>{confidence:.1f}%</strong></p>
            </div>
            """, unsafe_allow_html=True)

        st.divider()

        # 技術指標
        st.subheader("📊 技術指標")

        indicators = analysis.get('indicators', {})
        signals = analysis.get('signals', {})

        col_ind1, col_ind2, col_ind3, col_ind4 = st.columns(4)

        with col_ind1:
            price = indicators.get('price', 0)
            ma20 = indicators.get('MA20')
            if ma20:
                price_vs_ma = ((price - ma20) / ma20) * 100
                st.metric("收盤價", f"${price:.2f}", f"{price_vs_ma:+.2f}% vs MA20")
            else:
                st.metric("收盤價", f"${price:.2f}")

        with col_ind2:
            rsi = indicators.get('RSI')
            if rsi:
                rsi_status = "超賣" if rsi < 30 else "超買" if rsi > 70 else "正常"
                st.metric("RSI (14)", f"{rsi:.1f}", rsi_status)

        with col_ind3:
            macd = indicators.get('MACD')
            macd_signal = indicators.get('MACD_Signal')
            if macd is not None and macd_signal is not None:
                macd_diff = macd - macd_signal
                st.metric("MACD", f"{macd:.3f}", f"信號差: {macd_diff:+.3f}")

        with col_ind4:
            bb_pos = indicators.get('BB_Position')
            if bb_pos is not None:
                bb_status = "接近上軌" if bb_pos > 0.8 else "接近下軌" if bb_pos < 0.2 else "通道中間"
                st.metric("布林位置", f"{bb_pos*100:.1f}%", bb_status)

        # 信號詳情
        st.subheader("📡 策略信號")

        signal_data = []
        signal_names = {
            'MA': ('均線交叉', '短期均線 vs 長期均線'),
            'RSI': ('RSI 超買超賣', 'RSI < 30 買入, > 70 賣出'),
            'MACD': ('MACD 交叉', 'MACD 與信號線交叉'),
            'BB': ('布林通道', '價格觸及通道邊界')
        }

        for key, (name, desc) in signal_names.items():
            signal_val = signals.get(key, 0)
            if signal_val == 1:
                signal_text = "🟢 買入"
            elif signal_val == -1:
                signal_text = "🔴 賣出"
            else:
                signal_text = "🟡 觀望"

            signal_data.append({
                "策略": name,
                "說明": desc,
                "信號": signal_text
            })

        st.dataframe(pd.DataFrame(signal_data), use_container_width=True, hide_index=True)

        # 分析理由
        st.subheader("💡 分析理由")
        reasons = analysis.get('reasons', [])
        if reasons:
            for reason in reasons:
                st.markdown(f"• {reason}")
        else:
            st.info("無特殊信號")

        # 回測結果
        st.subheader("📈 策略回測績效 (過去一年)")

        backtest = analysis.get('backtest', {})
        if backtest:
            bt_data = []
            for strategy, results in backtest.items():
                bt_data.append({
                    "策略": signal_names.get(strategy, (strategy, ''))[0],
                    "年報酬率": f"{results['total_return']:.2f}%",
                    "勝率": f"{results['win_rate']:.1f}%",
                    "交易次數": results['total_trades'],
                    "夏普比率": f"{results['sharpe_ratio']:.2f}",
                    "最大回撤": f"{results['max_drawdown']:.2f}%",
                    "買入持有": f"{results['buy_hold_return']:.2f}%"
                })

            st.dataframe(pd.DataFrame(bt_data), use_container_width=True, hide_index=True)

            # 比較圖
            st.markdown("**策略報酬 vs 買入持有**")
            chart_data = []
            for strategy, results in backtest.items():
                chart_data.append({
                    "策略": signal_names.get(strategy, (strategy, ''))[0],
                    "策略報酬": results['total_return'],
                    "買入持有": results['buy_hold_return']
                })

            df_chart = pd.DataFrame(chart_data)
            st.bar_chart(df_chart.set_index("策略"))


def render_top_picks(analyzer: TechnicalAnalyzer):
    """買賣排行榜"""
    st.subheader("🏆 今日買賣建議排行")

    with st.spinner("正在分析所有股票..."):
        buy_picks, sell_picks = analyzer.get_top_picks(n=10)

    col_buy, col_sell = st.columns(2)

    with col_buy:
        st.markdown("### 🟢 買進標的 TOP 10")

        if buy_picks:
            buy_data = []
            for i, pick in enumerate(buy_picks, 1):
                stock_info = get_stock_info(pick['symbol'])
                name = stock_info['name'] if stock_info else pick['symbol']
                signals = pick.get('signals', {})
                indicators = pick.get('indicators', {})

                buy_data.append({
                    "排名": i,
                    "代碼": pick['symbol'],
                    "名稱": name[:10] if name else "-",
                    "建議": pick.get('recommendation_text', '-'),
                    "信心度": f"{pick['confidence']:.0f}%",
                    "RSI": f"{indicators.get('RSI', 0):.0f}" if indicators.get('RSI') else "-",
                    "綜合分數": f"{pick['combined_signal']:.2f}"
                })

            st.dataframe(pd.DataFrame(buy_data), use_container_width=True, hide_index=True)

            # 詳細原因
            with st.expander("📋 詳細分析"):
                for pick in buy_picks[:5]:
                    st.markdown(f"**{pick['symbol']}**")
                    for reason in pick.get('reasons', [])[:3]:
                        st.markdown(f"  • {reason}")
                    st.markdown("---")
        else:
            st.info("目前沒有強烈買進信號的股票")

    with col_sell:
        st.markdown("### 🔴 賣出標的 TOP 10")

        if sell_picks:
            sell_data = []
            for i, pick in enumerate(sell_picks, 1):
                stock_info = get_stock_info(pick['symbol'])
                name = stock_info['name'] if stock_info else pick['symbol']
                signals = pick.get('signals', {})
                indicators = pick.get('indicators', {})

                sell_data.append({
                    "排名": i,
                    "代碼": pick['symbol'],
                    "名稱": name[:10] if name else "-",
                    "建議": pick.get('recommendation_text', '-'),
                    "信心度": f"{pick['confidence']:.0f}%",
                    "RSI": f"{indicators.get('RSI', 0):.0f}" if indicators.get('RSI') else "-",
                    "綜合分數": f"{pick['combined_signal']:.2f}"
                })

            st.dataframe(pd.DataFrame(sell_data), use_container_width=True, hide_index=True)

            # 詳細原因
            with st.expander("📋 詳細分析"):
                for pick in sell_picks[:5]:
                    st.markdown(f"**{pick['symbol']}**")
                    for reason in pick.get('reasons', [])[:3]:
                        st.markdown(f"  • {reason}")
                    st.markdown("---")
        else:
            st.info("目前沒有強烈賣出信號的股票")


def render_strategy_backtest(analyzer: TechnicalAnalyzer):
    """策略回測 - 直觀顯示買賣點和獲利"""
    st.subheader("📈 策略回測模擬")

    # 策略類型選擇
    strategy_type = st.radio(
        "選擇策略類型",
        ["📊 單一股票策略", "🔄 動態換股策略"],
        horizontal=True
    )

    st.divider()

    if strategy_type == "📊 單一股票策略":
        render_single_stock_backtest(analyzer)
    else:
        render_momentum_rotation()


def render_single_stock_backtest(analyzer: TechnicalAnalyzer):
    """單一股票策略回測"""
    st.info("💡 假設初始資金 10 萬元，根據策略信號買進賣出，看看能賺多少錢")

    # 取得股票清單
    watchlist = get_watchlist()
    if not watchlist:
        st.warning("追蹤清單為空")
        return

    col1, col2, col3 = st.columns(3)

    with col1:
        stock_options = {f"{s['symbol']} - {s['name'][:12] if s['name'] else s['symbol']}": s["symbol"] for s in watchlist}
        selected_stock = st.selectbox("選擇股票", list(stock_options.keys()), key="bt_stock")
        selected_symbol = stock_options[selected_stock]

    with col2:
        strategy_options = {
            "📈 買入持有 (Buy & Hold)": "BH",
            "均線交叉 (MA5/MA20)": "MA",
            "RSI 超買超賣": "RSI",
            "MACD 金死叉": "MACD",
            "布林通道突破": "BB"
        }
        selected_strategy_name = st.selectbox("選擇策略", list(strategy_options.keys()))
        selected_strategy = strategy_options[selected_strategy_name]

    with col3:
        initial_capital = st.number_input("初始資金", value=100000, step=10000, format="%d")

    # 回測期間選擇
    st.markdown("##### 回測期間")
    date_col1, date_col2 = st.columns(2)
    with date_col1:
        single_start_year = st.selectbox("起始年份", [2021, 2022, 2023, 2024, 2025], index=0, key="single_start_year")
    with date_col2:
        single_end_year = st.selectbox("結束年份", [2021, 2022, 2023, 2024, 2025, 2026], index=5, key="single_end_year")

    if single_start_year > single_end_year:
        st.error("起始年份不能大於結束年份")
        return

    start_date_str = f"{single_start_year}-01-01"
    end_date_str = f"{single_end_year}-12-31" if single_end_year < 2026 else date.today().strftime("%Y-%m-%d")

    if st.button("🚀 開始回測", type="primary", use_container_width=True):
        with st.spinner("回測計算中..."):
            # 根據策略選擇不同的回測方法
            if selected_strategy == "BH":
                # Buy and Hold 策略
                portfolio = PortfolioStrategy(str(FINANCE_DB_PATH))
                result = portfolio.buy_and_hold(
                    selected_symbol, initial_capital,
                    start_date=start_date_str, end_date=end_date_str
                )
            else:
                # 技術指標策略
                result = analyzer.get_trade_history(
                    selected_symbol, selected_strategy, initial_capital,
                    start_date=start_date_str, end_date=end_date_str
                )

        trades = result['trades']
        equity_curve = result['equity_curve']
        summary = result['summary']
        df = result.get('df', pd.DataFrame())

        if not trades:
            st.warning(f"此策略在 {single_start_year}-{single_end_year} 期間沒有產生任何交易信號")
            return

        # ========== 總結卡片 ==========
        st.divider()
        st.subheader("💰 回測結果總結")

        col_s1, col_s2, col_s3, col_s4 = st.columns(4)

        total_profit = summary['total_profit']
        total_return = summary['total_return_pct']

        with col_s1:
            if total_profit >= 0:
                st.metric("總獲利", f"${total_profit:,.0f}", f"+{total_return:.1f}%")
            else:
                st.metric("總虧損", f"${total_profit:,.0f}", f"{total_return:.1f}%")

        with col_s2:
            if selected_strategy == "BH":
                st.metric("持有天數", f"{summary.get('holding_days', 0)} 天")
            else:
                st.metric("交易次數", f"{summary['total_trades']} 次",
                          f"勝率 {summary.get('win_rate', 0):.0f}%")

        with col_s3:
            st.metric("最終資金", f"${summary['final_equity']:,.0f}",
                      f"初始 ${initial_capital:,}")

        with col_s4:
            if selected_strategy != "BH":
                buy_hold = summary.get('buy_hold_return', 0)
                diff = total_return - buy_hold
                st.metric("買入持有報酬", f"{buy_hold:.1f}%",
                          f"策略{'勝' if diff > 0 else '負'} {abs(diff):.1f}%")
            else:
                max_dd = summary.get('max_drawdown', 0)
                st.metric("最大回撤", f"{max_dd:.1f}%")

        # ========== 價格圖表 + 買賣點 ==========
        st.divider()
        st.subheader("📊 買賣點視覺化")

        # 建立圖表
        fig = make_subplots(
            rows=2, cols=1,
            shared_xaxes=True,
            vertical_spacing=0.1,
            row_heights=[0.7, 0.3],
            subplot_titles=(f"{selected_symbol} 價格與買賣點", "資金曲線")
        )

        # 價格線
        fig.add_trace(
            go.Scatter(
                x=df['date'],
                y=df['close'],
                mode='lines',
                name='股價',
                line=dict(color='#1f77b4', width=1.5)
            ),
            row=1, col=1
        )

        # 買入點
        buy_dates = []
        buy_prices = []
        buy_texts = []
        for trade in trades:
            buy_dates.append(trade['entry_date'])
            buy_prices.append(trade['entry_price'])
            buy_texts.append(f"買入 ${trade['entry_price']:.2f}<br>{trade['shares']}股")

        fig.add_trace(
            go.Scatter(
                x=buy_dates,
                y=buy_prices,
                mode='markers',
                name='買入',
                marker=dict(
                    symbol='triangle-up',
                    size=15,
                    color='green'
                ),
                text=buy_texts,
                hoverinfo='text+x'
            ),
            row=1, col=1
        )

        # 賣出點
        sell_dates = []
        sell_prices = []
        sell_texts = []
        for trade in trades:
            if trade.get('exit_date') != '持有中':
                sell_dates.append(trade['exit_date'])
                sell_prices.append(trade['exit_price'])
                profit_sign = '+' if trade['profit'] >= 0 else ''
                sell_texts.append(f"賣出 ${trade['exit_price']:.2f}<br>獲利 {profit_sign}${trade['profit']:.0f}")

        fig.add_trace(
            go.Scatter(
                x=sell_dates,
                y=sell_prices,
                mode='markers',
                name='賣出',
                marker=dict(
                    symbol='triangle-down',
                    size=15,
                    color='red'
                ),
                text=sell_texts,
                hoverinfo='text+x'
            ),
            row=1, col=1
        )

        # 資金曲線
        eq_dates = [e['date'] for e in equity_curve]
        eq_values = [e['equity'] for e in equity_curve]

        fig.add_trace(
            go.Scatter(
                x=eq_dates,
                y=eq_values,
                mode='lines',
                name='資金',
                line=dict(color='#ff7f0e', width=2),
                fill='tozeroy',
                fillcolor='rgba(255, 127, 14, 0.1)'
            ),
            row=2, col=1
        )

        # 初始資金線
        fig.add_hline(y=initial_capital, line_dash="dash", line_color="gray",
                      annotation_text=f"初始資金 ${initial_capital:,}", row=2, col=1)

        fig.update_layout(
            height=600,
            showlegend=True,
            legend=dict(orientation="h", yanchor="bottom", y=1.02),
            hovermode='x unified'
        )

        fig.update_yaxes(title_text="股價", row=1, col=1)
        fig.update_yaxes(title_text="資金", row=2, col=1)

        st.plotly_chart(fig, use_container_width=True)

        # ========== 交易明細 ==========
        st.divider()
        st.subheader("📋 交易明細")

        trade_data = []
        for i, trade in enumerate(trades, 1):
            profit_display = f"+${trade['profit']:,.0f}" if trade['profit'] >= 0 else f"-${abs(trade['profit']):,.0f}"
            pct_display = f"+{trade['profit_pct']:.1f}%" if trade['profit_pct'] >= 0 else f"{trade['profit_pct']:.1f}%"

            trade_data.append({
                "交易": f"#{i}",
                "買入日期": trade['entry_date'],
                "買入價": f"${trade['entry_price']:.2f}",
                "賣出日期": trade['exit_date'],
                "賣出價": f"${trade['exit_price']:.2f}",
                "股數": trade['shares'],
                "獲利": profit_display,
                "報酬率": pct_display,
                "結果": "🟢 獲利" if trade['profit'] >= 0 else "🔴 虧損"
            })

        df_trades = pd.DataFrame(trade_data)
        st.dataframe(df_trades, use_container_width=True, hide_index=True)

        # ========== 策略說明 ==========
        st.divider()
        with st.expander("📖 策略說明"):
            strategy_explanations = {
                "BH": """
                **買入持有策略 (Buy and Hold)**

                - **買入時機**: 第一天開盤買入
                - **賣出時機**: 持有到最後一天

                最簡單的長期投資策略，適合看好長期趨勢的投資人。
                巴菲特名言：「如果你不願意持有一檔股票十年，那就連十分鐘都不要持有。」
                """,
                "MA": """
                **均線交叉策略 (MA5/MA20)**

                - **買入時機**: 當 5 日均線從下方穿越 20 日均線（黃金交叉）
                - **賣出時機**: 當 5 日均線從上方跌破 20 日均線（死亡交叉）

                這是最常見的趨勢跟隨策略，適合波段操作。
                """,
                "RSI": """
                **RSI 超買超賣策略**

                - **買入時機**: 當 RSI 低於 30（超賣區）
                - **賣出時機**: 當 RSI 高於 70（超買區）

                RSI 是動量指標，適合震盪盤整時使用。
                """,
                "MACD": """
                **MACD 金死叉策略**

                - **買入時機**: 當 MACD 線從下方穿越信號線（金叉）
                - **賣出時機**: 當 MACD 線從上方跌破信號線（死叉）

                MACD 結合趨勢和動量，是較為靈敏的指標。
                """,
                "BB": """
                **布林通道突破策略**

                - **買入時機**: 當股價跌破布林下軌（超賣反彈）
                - **賣出時機**: 當股價突破布林上軌（超買回落）

                布林通道利用統計學原理判斷價格極端位置。
                """
            }
            st.markdown(strategy_explanations.get(selected_strategy, ""))


def render_momentum_rotation():
    """動態換股策略回測"""
    st.info("""
    💡 **動態換股策略 (Momentum Rotation)**

    自動分析股票池中所有股票的動能，定期選擇表現最強的股票持有。

    - 計算每檔股票過去 N 天的報酬率（動能）
    - 選擇動能最強的前 K 檔股票
    - 平均分配資金
    - 每 M 天重新調整持股
    """)

    col1, col2, col3 = st.columns(3)

    with col1:
        initial_capital = st.number_input("初始資金", value=100000, step=10000, format="%d", key="mom_capital")
        top_n = st.slider("持有股票數", min_value=3, max_value=10, value=5, key="mom_top_n")

    with col2:
        market_options = {"美股": "US", "台股": "TW", "ETF": "ETF"}
        selected_market_name = st.selectbox("選擇市場", list(market_options.keys()), key="mom_market")
        selected_market = market_options[selected_market_name]

        rebalance_days = st.slider("調倉週期（天）", min_value=5, max_value=60, value=20, key="mom_rebal")

    with col3:
        lookback_days = st.slider("動能計算天數", min_value=5, max_value=60, value=20, key="mom_lookback")

    # 回測期間選擇
    st.markdown("##### 回測期間")
    mom_date_col1, mom_date_col2 = st.columns(2)
    with mom_date_col1:
        mom_start_year = st.selectbox("起始年份", [2021, 2022, 2023, 2024, 2025], index=0, key="mom_start_year")
    with mom_date_col2:
        mom_end_year = st.selectbox("結束年份", [2021, 2022, 2023, 2024, 2025, 2026], index=5, key="mom_end_year")

    if mom_start_year > mom_end_year:
        st.error("起始年份不能大於結束年份")
        return

    mom_start_date = f"{mom_start_year}-01-01"
    mom_end_date = f"{mom_end_year}-12-31" if mom_end_year < 2026 else date.today().strftime("%Y-%m-%d")

    # 進階選項
    st.markdown("##### 進階選項")
    adv_col1, adv_col2 = st.columns(2)
    with adv_col1:
        use_vol_adjust = st.checkbox("📊 波動率校正", value=True, key="use_vol_adjust",
                                      help="使用風險調整後的動量指標，降低高波動股票的權重")
        vol_method = st.selectbox("校正方法", ["sharpe", "sortino", "vol_scaled"],
                                   index=0, key="vol_method",
                                   help="sharpe=夏普比率, sortino=索提諾比率, vol_scaled=波動率縮放")
    with adv_col2:
        run_robustness = st.checkbox("🔬 魯棒性檢測", value=False, key="run_robustness",
                                      help="測試不同參數組合的穩定性")
        run_walkforward = st.checkbox("📈 走動式評估", value=False, key="run_walkforward",
                                       help="使用滾動視窗進行樣本外測試")

    # 主要回測按鈕
    if st.button("🚀 開始動態換股回測", type="primary", use_container_width=True):
        portfolio = PortfolioStrategy(str(FINANCE_DB_PATH))

        with st.spinner(f"正在計算 {selected_market_name} 市場 ({mom_start_year}-{mom_end_year}) 的動態換股策略..."):
            if use_vol_adjust:
                result = portfolio.momentum_rotation_vol_adjusted(
                    symbols=None,
                    initial_capital=initial_capital,
                    top_n=top_n,
                    rebalance_days=rebalance_days,
                    lookback_days=lookback_days,
                    market=selected_market,
                    start_date=mom_start_date,
                    end_date=mom_end_date,
                    vol_adjust_method=vol_method
                )
            else:
                result = portfolio.momentum_rotation(
                    symbols=None,
                    initial_capital=initial_capital,
                    top_n=top_n,
                    rebalance_days=rebalance_days,
                    lookback_days=lookback_days,
                    market=selected_market,
                    start_date=mom_start_date,
                    end_date=mom_end_date
                )

        if 'error' in result:
            st.error(result['error'])
            return

        summary = result['summary']
        trades = result['trades']
        equity_curve = result['equity_curve']
        rebalance_records = result['rebalance_records']

        # ========== 總結卡片 ==========
        st.divider()
        st.subheader("💰 動態換股策略結果")

        col_s1, col_s2, col_s3, col_s4 = st.columns(4)

        total_profit = summary['total_profit']
        total_return = summary['total_return_pct']

        with col_s1:
            if total_profit >= 0:
                st.metric("總獲利", f"${total_profit:,.0f}", f"+{total_return:.1f}%")
            else:
                st.metric("總虧損", f"${total_profit:,.0f}", f"{total_return:.1f}%")

        with col_s2:
            st.metric("調倉次數", f"{summary['rebalance_count']} 次",
                      f"交易 {summary['total_trades']} 筆")

        with col_s3:
            st.metric("最終資金", f"${summary['final_equity']:,.0f}",
                      f"初始 ${initial_capital:,}")

        with col_s4:
            buy_hold = summary.get('buy_hold_return', 0)
            diff = total_return - buy_hold
            st.metric("等權持有報酬", f"{buy_hold:.1f}%",
                      f"策略{'勝' if diff > 0 else '負'} {abs(diff):.1f}%")

        # ========== 資金曲線圖表 ==========
        st.divider()
        st.subheader("📈 資金曲線")

        if equity_curve:
            eq_dates = [e['date'] for e in equity_curve]
            eq_values = [e['equity'] for e in equity_curve]

            fig = go.Figure()

            # 資金曲線
            fig.add_trace(
                go.Scatter(
                    x=eq_dates,
                    y=eq_values,
                    mode='lines',
                    name='動態換股策略',
                    line=dict(color='#1f77b4', width=2),
                    fill='tozeroy',
                    fillcolor='rgba(31, 119, 180, 0.1)'
                )
            )

            # 初始資金線
            fig.add_hline(y=initial_capital, line_dash="dash", line_color="gray",
                          annotation_text=f"初始資金 ${initial_capital:,}")

            # 標記調倉日
            rebal_dates = [r['date'] for r in rebalance_records]
            rebal_values = []
            for rd in rebal_dates:
                for eq in equity_curve:
                    if str(eq['date'])[:10] == rd:
                        rebal_values.append(eq['equity'])
                        break
                else:
                    rebal_values.append(None)

            fig.add_trace(
                go.Scatter(
                    x=rebal_dates,
                    y=rebal_values,
                    mode='markers',
                    name='調倉日',
                    marker=dict(symbol='diamond', size=10, color='orange')
                )
            )

            fig.update_layout(
                height=400,
                xaxis_title="日期",
                yaxis_title="資金",
                hovermode='x unified'
            )

            st.plotly_chart(fig, use_container_width=True)

        # ========== 調倉記錄 ==========
        st.divider()
        st.subheader("🔄 調倉記錄")

        if rebalance_records:
            rebal_data = []
            for i, rec in enumerate(rebalance_records, 1):
                # 處理 momentum 或 adjusted_momentum
                mom_data = rec.get('momentum') or rec.get('adjusted_momentum', {})
                top_stock = rec['selected'][0] if rec['selected'] else "-"
                top_mom = mom_data.get(top_stock, "-") if mom_data and top_stock != "-" else "-"

                rebal_data.append({
                    "次數": i,
                    "日期": rec['date'],
                    "選中股票": ", ".join(rec['selected'][:5]),
                    "動能最強": f"{top_stock} ({top_mom})" if top_stock != "-" else "-",
                    "組合價值": f"${rec['total_value']:,.0f}"
                })

            st.dataframe(pd.DataFrame(rebal_data), use_container_width=True, hide_index=True)

        # ========== 當前持股 ==========
        st.divider()
        st.subheader("📊 最終持股")

        final_holdings = summary.get('final_holdings', {})
        if final_holdings:
            holdings_data = []
            for sym, shares in final_holdings.items():
                holdings_data.append({
                    "股票": sym,
                    "股數": shares
                })
            st.dataframe(pd.DataFrame(holdings_data), use_container_width=True, hide_index=True)
        else:
            st.info("策略結束時已全部出清")

        # ========== 交易明細 ==========
        with st.expander("📋 詳細交易記錄"):
            if trades:
                trade_data = []
                for trade in trades[-50:]:  # 顯示最近50筆
                    # 處理 momentum 或 adjusted_momentum
                    mom_val = trade.get('momentum') or trade.get('adjusted_momentum', '-')
                    trade_data.append({
                        "日期": trade['date'],
                        "動作": "🟢 買入" if trade['action'] == 'BUY' else "🔴 賣出",
                        "股票": trade['symbol'],
                        "股數": trade['shares'],
                        "價格": f"${trade['price']:.2f}",
                        "金額": f"${trade['value']:,.0f}",
                        "原因": trade.get('reason', '-'),
                        "動能": mom_val
                    })

                st.dataframe(pd.DataFrame(trade_data), use_container_width=True, hide_index=True)
                st.caption(f"顯示最近 50 筆交易 (共 {len(trades)} 筆)")

        # ========== 策略說明 ==========
        st.divider()
        with st.expander("📖 策略說明"):
            st.markdown(f"""
            **動態換股策略 (Momentum Rotation)**

            本策略基於「動量效應」：過去表現好的股票，短期內傾向於繼續表現好。

            **參數設定:**
            - 股票池: {selected_market_name} 市場 ({summary.get('stock_pool_size', 0)} 檔)
            - 持有數量: {top_n} 檔
            - 調倉週期: 每 {rebalance_days} 天
            - 動能計算: 過去 {lookback_days} 天報酬率

            **策略邏輯:**
            1. 計算股票池中每檔股票過去 {lookback_days} 天的報酬率
            2. 選擇報酬率最高的前 {top_n} 檔
            3. 平均分配資金買入
            4. 每 {rebalance_days} 天重新計算，汰弱留強

            **優點:**
            - 自動追蹤市場熱點
            - 分散投資降低風險
            - 定期調整避免抱死

            **風險:**
            - 頻繁交易產生成本
            - 動能反轉時可能虧損
            - 過去績效不代表未來
            """)

    # ========== 魯棒性檢測 ==========
    if run_robustness:
        st.divider()
        st.subheader("🔬 魯棒性檢測")
        st.info("測試不同參數組合的績效穩定性")

        if st.button("執行魯棒性檢測", key="run_robust_btn"):
            robust_portfolio = PortfolioStrategy(str(FINANCE_DB_PATH))
            with st.spinner("正在進行參數敏感度分析... (可能需要數分鐘)"):
                robust_result = robust_portfolio.robustness_test(
                    symbols=None,
                    initial_capital=initial_capital,
                    market=selected_market,
                    start_date=mom_start_date,
                    end_date=mom_end_date
                )

            if 'error' in robust_result:
                st.error(robust_result['error'])
            else:
                robust_summary = robust_result['summary']

                st.markdown("#### 📊 檢測結果摘要")

                rcol1, rcol2, rcol3, rcol4 = st.columns(4)
                with rcol1:
                    st.metric("測試組合數", robust_summary['total_tests'])
                with rcol2:
                    vol_benefit = robust_summary['vol_adjustment_benefit']
                    st.metric("波動率校正效益",
                              f"{vol_benefit:+.3f}",
                              "夏普提升" if vol_benefit > 0 else "夏普下降")
                with rcol3:
                    st.metric("原始策略正報酬比例", f"{robust_summary['raw_positive_pct']:.1f}%")
                with rcol4:
                    st.metric("校正策略正報酬比例", f"{robust_summary['vol_positive_pct']:.1f}%")

                st.markdown("#### 🏆 最佳參數")
                best_col1, best_col2 = st.columns(2)
                with best_col1:
                    st.markdown("**原始動量策略**")
                    st.write(f"- top_n: {robust_summary['best_raw_params']['top_n']}")
                    st.write(f"- 調倉週期: {robust_summary['best_raw_params']['rebalance_days']} 天")
                    st.write(f"- 回顧天數: {robust_summary['best_raw_params']['lookback_days']} 天")
                    st.write(f"- 夏普比率: {robust_summary['best_raw_sharpe']:.3f}")
                    st.write(f"- 報酬率: {robust_summary['best_raw_return']:.2f}%")

                with best_col2:
                    st.markdown("**波動率校正策略**")
                    st.write(f"- top_n: {robust_summary['best_vol_params']['top_n']}")
                    st.write(f"- 調倉週期: {robust_summary['best_vol_params']['rebalance_days']} 天")
                    st.write(f"- 回顧天數: {robust_summary['best_vol_params']['lookback_days']} 天")
                    st.write(f"- 夏普比率: {robust_summary['best_vol_sharpe']:.3f}")
                    st.write(f"- 報酬率: {robust_summary['best_vol_return']:.2f}%")

                # 參數敏感度圖
                st.markdown("#### 📈 參數敏感度分析")

                sensitivity = robust_result['sensitivity']

                # Top N 敏感度
                fig_sens = go.Figure()

                top_n_vals = list(sensitivity['top_n']['raw_sharpe'].keys())
                raw_sharpes = [sensitivity['top_n']['raw_sharpe'][k] for k in top_n_vals]
                vol_sharpes = [sensitivity['top_n']['vol_sharpe'][k] for k in top_n_vals]

                fig_sens.add_trace(go.Bar(name='原始動量', x=[str(x) for x in top_n_vals], y=raw_sharpes))
                fig_sens.add_trace(go.Bar(name='波動率校正', x=[str(x) for x in top_n_vals], y=vol_sharpes))

                fig_sens.update_layout(
                    title="Top N 參數對夏普比率的影響",
                    xaxis_title="持股數量 (Top N)",
                    yaxis_title="平均夏普比率",
                    barmode='group',
                    height=300
                )
                st.plotly_chart(fig_sens, use_container_width=True)

                # 詳細結果表格
                with st.expander("📋 完整測試結果"):
                    param_df = pd.DataFrame(robust_result['param_results'])
                    param_df = param_df.round(3)
                    st.dataframe(param_df, use_container_width=True, hide_index=True)

    # ========== 走動式評估 ==========
    if run_walkforward:
        st.divider()
        st.subheader("📈 走動式評估 (Walk-Forward Analysis)")
        st.info("使用滾動視窗進行樣本外測試，驗證策略的穩定性")

        wf_col1, wf_col2 = st.columns(2)
        with wf_col1:
            wf_train = st.slider("訓練期 (月)", min_value=3, max_value=12, value=6, key="wf_train")
        with wf_col2:
            wf_test = st.slider("測試期 (月)", min_value=1, max_value=6, value=3, key="wf_test")

        if st.button("執行走動式評估", key="run_wf_btn"):
            wf_portfolio = PortfolioStrategy(str(FINANCE_DB_PATH))
            with st.spinner("正在進行走動式評估... (可能需要較長時間)"):
                wf_result = wf_portfolio.walk_forward_analysis(
                    symbols=None,
                    initial_capital=initial_capital,
                    market=selected_market,
                    start_date=mom_start_date,
                    end_date=mom_end_date,
                    train_months=wf_train,
                    test_months=wf_test,
                    vol_adjusted=use_vol_adjust
                )

            if 'error' in wf_result.get('summary', {}):
                st.error(wf_result['summary']['error'])
            else:
                wf_summary = wf_result['summary']

                st.markdown("#### 📊 走動式評估結果")

                wfcol1, wfcol2, wfcol3, wfcol4 = st.columns(4)
                with wfcol1:
                    st.metric("評估視窗數", wf_summary['total_windows'])
                with wfcol2:
                    st.metric("平均測試報酬", f"{wf_summary['avg_test_return_pct']:.2f}%")
                with wfcol3:
                    st.metric("平均測試夏普", f"{wf_summary['avg_test_sharpe']:.3f}")
                with wfcol4:
                    st.metric("一致性 (正報酬比例)", f"{wf_summary['consistency_pct']:.1f}%",
                              f"{wf_summary['positive_windows']}/{wf_summary['total_windows']} 視窗")

                # 各視窗結果
                st.markdown("#### 📋 各視窗結果")
                window_df = pd.DataFrame(wf_result['window_results'])
                window_df['test_return'] = window_df['test_return'].round(2)
                window_df['test_sharpe'] = window_df['test_sharpe'].round(3)
                window_df['test_max_dd'] = window_df['test_max_dd'].round(2)
                window_df['train_sharpe'] = window_df['train_sharpe'].round(3)

                # 根據報酬率著色
                def color_returns(val):
                    if isinstance(val, (int, float)):
                        color = 'green' if val > 0 else 'red'
                        return f'color: {color}'
                    return ''

                styled_df = window_df.style.applymap(color_returns, subset=['test_return'])
                st.dataframe(window_df, use_container_width=True, hide_index=True)

                # 視覺化各視窗報酬
                fig_wf = go.Figure()
                fig_wf.add_trace(go.Bar(
                    x=window_df['test_period'],
                    y=window_df['test_return'],
                    marker_color=['green' if r > 0 else 'red' for r in window_df['test_return']],
                    name='測試期報酬'
                ))
                fig_wf.update_layout(
                    title="各視窗測試期報酬率",
                    xaxis_title="測試期間",
                    yaxis_title="報酬率 (%)",
                    height=350
                )
                st.plotly_chart(fig_wf, use_container_width=True)


def render_watchlist_page():
    st.title("📋 股票追蹤清單")

    # 檢查金融資料庫是否存在
    if not FINANCE_DB_PATH.exists():
        st.error("找不到金融資料庫 (finance.db)")
        return

    # 取得追蹤清單
    watchlist = get_watchlist()
    if not watchlist:
        st.warning("追蹤清單為空")
        return

    # 依市場分組
    us_stocks = [s for s in watchlist if s.get("market") == "US"]
    tw_stocks = [s for s in watchlist if s.get("market") == "TW"]
    etf_stocks = [s for s in watchlist if s.get("market") == "ETF"]
    index_stocks = [s for s in watchlist if s.get("market") == "INDEX"]
    other_stocks = [s for s in watchlist if s.get("market") not in ["US", "TW", "ETF", "INDEX"]]

    # 統計
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("總股票數", len(watchlist))
    with col2:
        st.metric("美股", len(us_stocks))
    with col3:
        st.metric("台股", len(tw_stocks))
    with col4:
        st.metric("ETF/指數", len(etf_stocks) + len(index_stocks))

    st.divider()

    # 分頁顯示
    tab1, tab2, tab3, tab4 = st.tabs([
        f"🇺🇸 美股 ({len(us_stocks)})",
        f"🇹🇼 台股 ({len(tw_stocks)})",
        f"📊 ETF ({len(etf_stocks)})",
        f"📈 指數 ({len(index_stocks)})"
    ])

    def render_stock_table(stocks: list, show_market: bool = False):
        """渲染股票表格"""
        if not stocks:
            st.info("目前沒有股票")
            return

        # 依產業分組
        by_sector = {}
        for s in stocks:
            sec = s.get("sector") or "未分類"
            if sec not in by_sector:
                by_sector[sec] = []
            by_sector[sec].append(s)

        # 產業篩選
        sectors = ["全部"] + sorted(by_sector.keys())
        sector_filter = st.selectbox("產業篩選", sectors, key=f"sector_{id(stocks)}")

        if sector_filter != "全部":
            by_sector = {sector_filter: by_sector.get(sector_filter, [])}

        for sector, sector_stocks in sorted(by_sector.items()):
            with st.expander(f"**{sector}** ({len(sector_stocks)} 檔)", expanded=True):
                table_data = []
                for s in sector_stocks:
                    row = {
                        "代碼": s["symbol"],
                        "名稱": s.get("name") or s["symbol"],
                        "細分產業": s.get("industry") or "-",
                    }
                    if show_market:
                        row["市場"] = s.get("market") or "-"
                    table_data.append(row)

                df = pd.DataFrame(table_data)
                st.dataframe(df, use_container_width=True, hide_index=True)

    # Tab 1: 美股
    with tab1:
        st.subheader("🇺🇸 美股清單")
        render_stock_table(us_stocks)

    # Tab 2: 台股
    with tab2:
        st.subheader("🇹🇼 台股清單")
        render_stock_table(tw_stocks)

    # Tab 3: ETF
    with tab3:
        st.subheader("📊 ETF 清單")
        if etf_stocks:
            table_data = [{
                "代碼": s["symbol"],
                "名稱": s.get("name") or s["symbol"],
                "說明": s.get("description") or "-"
            } for s in etf_stocks]
            st.dataframe(pd.DataFrame(table_data), use_container_width=True, hide_index=True)
        else:
            st.info("目前沒有 ETF")

    # Tab 4: 指數
    with tab4:
        st.subheader("📈 指數清單")
        if index_stocks:
            table_data = [{
                "代碼": s["symbol"],
                "名稱": s.get("name") or s["symbol"],
            } for s in index_stocks]
            st.dataframe(pd.DataFrame(table_data), use_container_width=True, hide_index=True)
        else:
            st.info("目前沒有指數")

    # 產業分佈圖
    st.divider()
    st.subheader("📊 產業分佈")

    col_chart1, col_chart2 = st.columns(2)

    with col_chart1:
        st.markdown("**依市場**")
        market_df = pd.DataFrame([
            {"市場": k, "數量": v} for k, v in sorted(markets.items(), key=lambda x: -x[1])
        ])
        st.bar_chart(market_df.set_index("市場"))

    with col_chart2:
        st.markdown("**依產業**")
        # 只顯示前10大產業
        top_sectors = sorted(sectors.items(), key=lambda x: -x[1])[:10]
        sector_df = pd.DataFrame([
            {"產業": k, "數量": v} for k, v in top_sectors
        ])
        st.bar_chart(sector_df.set_index("產業"))


# ========== 總經分析頁面 ==========
def render_macro_analysis_page():
    """總經分析與市場週期頁面"""
    st.title("🌍 總經分析與市場週期")

    # 初始化
    try:
        macro_db = MacroDatabase()
        cycle_analyzer = MarketCycleAnalyzer(db=macro_db)
        strategy_selector = CycleBasedStrategySelector(macro_db=macro_db)
    except Exception as e:
        st.error(f"初始化失敗: {e}")
        st.info("請先執行 `python macro_scheduler.py --full` 收集總經數據")
        return

    # 取得當前週期
    try:
        current_cycle = cycle_analyzer.get_current_cycle()
        current_strategy = strategy_selector.get_current_strategy()
    except Exception as e:
        st.warning(f"無法取得週期分析: {e}")
        st.info("請先執行 `python macro_scheduler.py --full` 收集總經數據")
        current_cycle = None
        current_strategy = None

    # 頂部週期燈號
    if current_cycle:
        col1, col2, col3, col4 = st.columns(4)

        with col1:
            phase_color = current_cycle.get("phase_color", "#888888")
            st.markdown(f"""
            <div style="background-color: {phase_color}; padding: 20px; border-radius: 10px; text-align: center;">
                <h2 style="color: white; margin: 0;">{current_cycle.get('phase_emoji', '')} {current_cycle.get('phase_name', current_cycle['phase'])}</h2>
                <p style="color: white; margin: 5px 0 0 0;">當前市場週期</p>
            </div>
            """, unsafe_allow_html=True)

        with col2:
            score = current_cycle.get("score", 0)
            score_color = "#00C851" if score > 0 else "#ff4444" if score < 0 else "#ffbb33"
            st.metric("週期分數", f"{score:.2f}", delta=None)
            st.progress((score + 1) / 2)  # 轉換 -1~1 到 0~1

        with col3:
            confidence = current_cycle.get("confidence", 0)
            st.metric("判斷信心度", f"{confidence:.0%}")

        with col4:
            if current_strategy:
                st.metric("建議策略", current_strategy["strategy"]["name"])

    st.divider()

    # 分頁
    tab1, tab2, tab3, tab4, tab5 = st.tabs(["📊 市場週期", "📈 總經指標", "📉 歷史趨勢", "💡 策略建議", "🔬 策略回測"])

    # Tab 1: 市場週期
    with tab1:
        render_macro_cycle_tab(current_cycle, macro_db)

    # Tab 2: 總經指標
    with tab2:
        render_macro_indicators_tab(macro_db)

    # Tab 3: 歷史趨勢
    with tab3:
        render_macro_history_tab(macro_db)

    # Tab 4: 策略建議
    with tab4:
        render_macro_strategy_tab(current_strategy, strategy_selector)

    # Tab 5: 策略回測
    with tab5:
        render_backtest_tab(macro_db)


def render_macro_cycle_tab(current_cycle, macro_db):
    """市場週期分頁"""
    if not current_cycle:
        st.warning("尚無週期分析資料")
        return

    st.subheader("週期階段說明")
    st.markdown(f"**{current_cycle.get('phase_description', '')}**")

    st.divider()
    st.subheader("各維度分析")

    signals = current_cycle.get("signals", {})
    weights = current_cycle.get("weights", {})

    # 顯示各維度分析結果
    dimension_names = {
        "yield_curve": "殖利率曲線",
        "employment": "就業市場",
        "growth": "經濟成長",
        "inflation": "通貨膨脹",
        "sentiment": "市場情緒"
    }

    cols = st.columns(len(signals))
    for i, (dim, data) in enumerate(signals.items()):
        with cols[i]:
            dim_name = dimension_names.get(dim, dim)
            score = data.get("score", 0)
            signal = data.get("signal", "N/A")
            weight = weights.get(dim, 0)

            # 顏色
            if score > 0.3:
                color = "#00C851"
            elif score > 0:
                color = "#8BC34A"
            elif score > -0.3:
                color = "#ffbb33"
            else:
                color = "#ff4444"

            st.markdown(f"""
            <div style="background-color: {color}20; border-left: 4px solid {color}; padding: 15px; border-radius: 5px;">
                <h4 style="margin: 0;">{dim_name}</h4>
                <p style="margin: 5px 0; font-size: 24px; font-weight: bold;">{score:.2f}</p>
                <p style="margin: 0; font-size: 12px;">信號: {signal}</p>
                <p style="margin: 0; font-size: 12px;">權重: {weight:.0%}</p>
            </div>
            """, unsafe_allow_html=True)

            # 顯示詳細資料
            details = data.get("details", {})
            if isinstance(details, dict):
                with st.expander("詳細數據"):
                    for key, value in details.items():
                        if value is not None:
                            if isinstance(value, float):
                                st.write(f"**{key}**: {value:.2f}")
                            else:
                                st.write(f"**{key}**: {value}")

    # 週期歷史
    st.divider()
    st.subheader("週期歷史記錄")

    # 年份選擇器
    cycle_col1, cycle_col2 = st.columns(2)
    with cycle_col1:
        cycle_start_year = st.selectbox("起始年份", [2021, 2022, 2023, 2024, 2025], index=0, key="cycle_start")
    with cycle_col2:
        cycle_end_year = st.selectbox("結束年份", [2021, 2022, 2023, 2024, 2025, 2026], index=5, key="cycle_end")

    if cycle_start_year > cycle_end_year:
        st.error("起始年份不能大於結束年份")
    else:
        try:
            backtester = CycleBacktester(macro_db=macro_db)
            start_date = date(cycle_start_year, 1, 1)
            end_date = date(cycle_end_year, 12, 31) if cycle_end_year < 2026 else date.today()

            cycles = backtester.get_historical_cycles(start_date, end_date)

            if cycles:
                history_df = pd.DataFrame(cycles)
                history_df["date"] = pd.to_datetime(history_df["date"])

                # 週期分數走勢圖 (帶顏色標記週期)
                fig = go.Figure()

                # 根據週期上色
                phase_colors = {
                    "EXPANSION": "#00C851",
                    "PEAK": "#ffbb33",
                    "CONTRACTION": "#ff4444",
                    "TROUGH": "#33b5e5"
                }

                from config.macro_indicators import MARKET_CYCLES
                for phase in phase_colors.keys():
                    phase_data = history_df[history_df["phase"] == phase]
                    if not phase_data.empty:
                        phase_info = MARKET_CYCLES.get(phase, {})
                        phase_name = phase_info.get("name", phase)

                        fig.add_trace(go.Scatter(
                            x=phase_data["date"],
                            y=phase_data["score"],
                            mode="markers",
                            name=f"{phase_info.get('emoji', '')} {phase_name}",
                            marker=dict(color=phase_colors[phase], size=8)
                        ))

                # 加入趨勢線
                fig.add_trace(go.Scatter(
                    x=history_df["date"],
                    y=history_df["score"],
                    mode="lines",
                    name="分數趨勢",
                    line=dict(color="rgba(100,100,100,0.3)", width=1),
                    showlegend=False
                ))

                fig.add_hline(y=0, line_dash="dash", line_color="gray", annotation_text="中性")
                fig.update_layout(
                    title=f"市場週期分數走勢 ({cycle_start_year}-{cycle_end_year})",
                    xaxis_title="日期",
                    yaxis_title="週期分數",
                    yaxis_range=[-0.5, 0.5],
                    height=450,
                    legend=dict(orientation="h", yanchor="bottom", y=1.02)
                )
                st.plotly_chart(fig, use_container_width=True)

                # 週期統計表
                st.markdown("#### 週期分佈統計")
                phase_counts = history_df["phase"].value_counts()
                total = len(history_df)

                col1, col2, col3, col4 = st.columns(4)
                cols = [col1, col2, col3, col4]
                for i, phase in enumerate(["EXPANSION", "PEAK", "CONTRACTION", "TROUGH"]):
                    with cols[i]:
                        count = phase_counts.get(phase, 0)
                        pct = count / total * 100 if total > 0 else 0
                        phase_info = MARKET_CYCLES.get(phase, {})
                        st.metric(
                            f"{phase_info.get('emoji', '')} {phase_info.get('name', phase)}",
                            f"{count} 個月",
                            f"{pct:.1f}%"
                        )
            else:
                st.info("尚無歷史記錄")
        except Exception as e:
            st.warning(f"無法載入週期歷史: {e}")


def render_macro_indicators_tab(macro_db):
    """總經指標分頁"""
    st.subheader("關鍵總經指標")

    # 取得所有最新數據
    all_data = macro_db.get_all_latest_data()

    if not all_data:
        st.warning("尚無總經數據，請先執行數據收集")
        return

    # 按類別分組
    categories = {}
    for series_id, data in all_data.items():
        category = data.get("category", "other")
        if category not in categories:
            categories[category] = []
        categories[category].append(data)

    category_names = {
        "yield_curve": "殖利率曲線",
        "employment": "就業市場",
        "growth": "經濟成長",
        "inflation": "通貨膨脹",
        "interest_rate": "利率政策",
        "sentiment": "市場情緒"
    }

    # 顯示各類別
    for category, items in categories.items():
        cat_name = category_names.get(category, category)
        st.markdown(f"### {cat_name}")

        cols = st.columns(min(len(items), 3))
        for i, item in enumerate(items):
            with cols[i % 3]:
                value = item.get("value", 0)
                change_pct = item.get("change_pct")
                name = item.get("name", item.get("series_id"))
                unit = item.get("unit", "")

                delta = f"{change_pct:+.2f}%" if change_pct else None
                st.metric(
                    label=name,
                    value=f"{value:.2f}" if isinstance(value, float) else str(value),
                    delta=delta
                )
                st.caption(f"更新: {item.get('date', 'N/A')}")

        st.divider()


def render_macro_history_tab(macro_db):
    """歷史趨勢分頁"""
    st.subheader("指標歷史走勢")

    # 選擇指標
    indicators = macro_db.get_indicators(active_only=True)
    if not indicators:
        st.warning("尚無指標資料")
        return

    indicator_options = {f"{i['name']} ({i['series_id']})": i['series_id'] for i in indicators}
    selected_names = st.multiselect(
        "選擇指標",
        options=list(indicator_options.keys()),
        default=list(indicator_options.keys())[:2]
    )

    # 時間範圍選擇
    col1, col2, col3 = st.columns(3)
    with col1:
        start_year = st.selectbox("起始年份", [2021, 2022, 2023, 2024, 2025], index=0)
    with col2:
        end_year = st.selectbox("結束年份", [2021, 2022, 2023, 2024, 2025, 2026], index=5)
    with col3:
        chart_type = st.selectbox("圖表類型", ["折線圖", "面積圖"], index=0)

    # 計算日期範圍
    start_date = date(start_year, 1, 1)
    end_date = date(end_year, 12, 31) if end_year < 2026 else date.today()

    if start_year > end_year:
        st.error("起始年份不能大於結束年份")
        return

    if not selected_names:
        st.info("請選擇至少一個指標")
        return

    # 繪製圖表
    fig = go.Figure()

    for name in selected_names:
        series_id = indicator_options[name]
        data = macro_db.get_macro_data(series_id, start_date=start_date, end_date=end_date)

        if data:
            df = pd.DataFrame(data)
            df["date"] = pd.to_datetime(df["date"])
            df = df.sort_values("date")

            if chart_type == "折線圖":
                fig.add_trace(go.Scatter(
                    x=df["date"],
                    y=df["value"],
                    mode="lines",
                    name=name.split(" (")[0]
                ))
            else:
                fig.add_trace(go.Scatter(
                    x=df["date"],
                    y=df["value"],
                    mode="lines",
                    fill="tozeroy",
                    name=name.split(" (")[0]
                ))

    fig.update_layout(
        title=f"指標走勢比較 ({start_year} - {end_year})",
        xaxis_title="日期",
        yaxis_title="數值",
        height=500,
        legend=dict(orientation="h", yanchor="bottom", y=1.02)
    )
    st.plotly_chart(fig, use_container_width=True)

    # 顯示統計摘要
    if selected_names:
        st.subheader("期間統計摘要")
        stats_data = []
        for name in selected_names:
            series_id = indicator_options[name]
            data = macro_db.get_macro_data(series_id, start_date=start_date, end_date=end_date)
            if data:
                values = [d["value"] for d in data if d["value"] is not None]
                if values:
                    stats_data.append({
                        "指標": name.split(" (")[0],
                        "起始值": f"{values[-1]:.2f}",
                        "最新值": f"{values[0]:.2f}",
                        "最高": f"{max(values):.2f}",
                        "最低": f"{min(values):.2f}",
                        "平均": f"{sum(values)/len(values):.2f}",
                        "變化": f"{values[0] - values[-1]:+.2f}"
                    })
        if stats_data:
            st.dataframe(pd.DataFrame(stats_data), use_container_width=True, hide_index=True)


def render_macro_strategy_tab(current_strategy, strategy_selector):
    """策略建議分頁 - 多維度評分系統"""
    if not current_strategy:
        st.warning("尚無策略建議")
        return

    strategy = current_strategy.get("strategy", {})
    allocation = current_strategy.get("allocation", {})

    st.subheader(f"當前建議: {strategy.get('name', 'N/A')}")
    st.markdown(f"**風險容忍度**: {strategy.get('risk_tolerance', 'N/A')}")

    col1, col2, col3 = st.columns([1, 1, 1])

    with col1:
        # 資產配置圓餅圖
        st.markdown("### 資產配置")
        chart_data = strategy_selector.get_allocation_chart_data()

        fig = go.Figure(data=[go.Pie(
            labels=chart_data["labels"],
            values=chart_data["values"],
            marker_colors=chart_data["colors"],
            hole=0.4
        )])
        fig.update_layout(
            height=280,
            margin=dict(t=0, b=0, l=0, r=0)
        )
        st.plotly_chart(fig, use_container_width=True)

    with col2:
        # 板塊偏好
        st.markdown("### 板塊配置")

        st.markdown("**偏好板塊:**")
        preferred = current_strategy.get("preferred_sectors", [])
        for sector in preferred:
            st.markdown(f"- 🟢 {sector}")

        st.markdown("**迴避板塊:**")
        avoid = current_strategy.get("avoid_sectors", [])
        for sector in avoid:
            st.markdown(f"- 🔴 {sector}")

    with col3:
        # 評分權重說明
        st.markdown("### 評分權重")
        st.markdown("""
        個股推薦依以下優先順序評分：

        1. **週期契合度** (30%)
           - 是否符合當前週期偏好板塊
        2. **稀缺性/護城河** (30%)
           - 利潤率、ROE、機構持股
        3. **未來發展性** (25%)
           - PEG、Forward PE折價、負債比
        4. **動能** (15%)
           - 技術分析信號
        """)

    # 股票推薦
    st.divider()
    st.subheader("個股推薦 (多維度評分)")

    try:
        recommendations = strategy_selector.get_stock_recommendations(limit=10)
        st.caption(f"共分析 {recommendations.get('total_analyzed', 0)} 支股票")

        col1, col2 = st.columns(2)

        with col1:
            st.markdown("#### 📈 買進推薦")
            buy_recs = recommendations.get("buy_recommendations", [])
            if buy_recs:
                for rec in buy_recs[:5]:
                    symbol = rec.get("symbol", "")
                    total_score = rec.get("total_score", 0)
                    sector = rec.get("sector", "N/A")
                    is_preferred = rec.get("in_preferred_sector", False)
                    scores = rec.get("scores", {})

                    pref_badge = "⭐" if is_preferred else ""

                    # 顯示總分和股票資訊
                    st.markdown(f"**{symbol}** {pref_badge} - 總分: **{total_score:.2f}**")
                    st.caption(f"板塊: {sector}")

                    # 展開顯示詳細評分
                    with st.expander(f"查看 {symbol} 評分詳情"):
                        for dim_name, dim_data in scores.items():
                            dim_labels = {
                                "cycle_fit": "週期契合度",
                                "moat": "稀缺性/護城河",
                                "growth": "未來發展性",
                                "momentum": "動能"
                            }
                            label = dim_labels.get(dim_name, dim_name)
                            score = dim_data.get("score", 0)
                            weight = dim_data.get("weight", 0)
                            reasons = dim_data.get("reasons", [])

                            # 分數顏色
                            if score >= 0.7:
                                color = "green"
                            elif score >= 0.5:
                                color = "orange"
                            else:
                                color = "red"

                            st.markdown(f"**{label}**: :{color}[{score:.2f}] (權重 {weight:.0%})")
                            for reason in reasons[:2]:
                                st.caption(f"  {reason}")
            else:
                st.info("目前無買進推薦")

        with col2:
            st.markdown("#### 📉 賣出警示")
            sell_recs = recommendations.get("sell_recommendations", [])
            if sell_recs:
                for rec in sell_recs[:5]:
                    symbol = rec.get("symbol", "")
                    total_score = rec.get("total_score", 0)
                    sector = rec.get("sector", "N/A")
                    in_avoid = rec.get("in_avoid_sector", False)
                    scores = rec.get("scores", {})

                    avoid_badge = "⚠️迴避板塊" if in_avoid else ""

                    st.markdown(f"**{symbol}** - 總分: **{total_score:.2f}** {avoid_badge}")
                    st.caption(f"板塊: {sector}")

                    with st.expander(f"查看 {symbol} 評分詳情"):
                        for dim_name, dim_data in scores.items():
                            dim_labels = {
                                "cycle_fit": "週期契合度",
                                "moat": "稀缺性/護城河",
                                "growth": "未來發展性",
                                "momentum": "動能"
                            }
                            label = dim_labels.get(dim_name, dim_name)
                            score = dim_data.get("score", 0)
                            reasons = dim_data.get("reasons", [])

                            if score >= 0.7:
                                color = "green"
                            elif score >= 0.5:
                                color = "orange"
                            else:
                                color = "red"

                            st.markdown(f"**{label}**: :{color}[{score:.2f}]")
                            for reason in reasons[:2]:
                                st.caption(f"  {reason}")
            else:
                st.info("目前無賣出警示")

    except Exception as e:
        st.error(f"取得推薦失敗: {e}")


def render_backtest_tab(macro_db):
    """策略回測分頁"""
    st.subheader("🔬 週期策略歷史回測")

    st.markdown("""
    回測說明：
    - 根據歷史總經數據判斷市場週期
    - 依據週期策略調整股票配置
    - 偏好週期相關板塊，避開不利板塊
    """)

    col1, col2, col3 = st.columns(3)

    with col1:
        start_year = st.selectbox("起始年份", [2021, 2022, 2023, 2024, 2025], index=0, key="bt_start")
    with col2:
        end_year = st.selectbox("結束年份", [2021, 2022, 2023, 2024, 2025, 2026], index=5, key="bt_end")
    with col3:
        initial_capital = st.number_input("初始資金", value=100000, step=10000)

    if start_year > end_year:
        st.error("起始年份不能大於結束年份")
    elif st.button("執行回測", type="primary"):
        with st.spinner("回測進行中..."):
            try:
                backtester = CycleBacktester(macro_db=macro_db)

                start_date = date(start_year, 1, 1)
                end_date = date(end_year, 12, 31) if end_year < 2026 else date.today()

                result = backtester.backtest_strategy(
                    start_date=start_date,
                    end_date=end_date,
                    initial_capital=initial_capital
                )

                if "error" in result:
                    st.error(result["error"])
                    return

                # 顯示績效指標
                st.divider()
                st.subheader("📊 回測績效")

                perf = result["performance"]

                col1, col2, col3, col4 = st.columns(4)
                with col1:
                    st.metric("總報酬率", f"{perf['total_return_pct']:.1f}%")
                with col2:
                    st.metric("年化報酬", f"{perf['annualized_return_pct']:.1f}%")
                with col3:
                    st.metric("最大回撤", f"{perf['max_drawdown_pct']:.1f}%")
                with col4:
                    st.metric("夏普比率", f"{perf['sharpe_ratio']:.2f}")

                col1, col2, col3, col4 = st.columns(4)
                with col1:
                    st.metric("初始資金", f"${perf['initial_capital']:,.0f}")
                with col2:
                    st.metric("期末價值", f"${perf['final_value']:,.0f}")
                with col3:
                    st.metric("勝率", f"{perf['win_rate_pct']:.1f}%")
                with col4:
                    st.metric("交易次數", perf['total_trades'])

                # 與基準比較
                benchmark = backtester.compare_with_benchmark(start_date, end_date)
                if "error" not in benchmark:
                    st.divider()
                    st.subheader("📌 與基準比較 (SPY 買入持有)")

                    col1, col2, col3 = st.columns(3)
                    with col1:
                        st.metric("策略報酬", f"{perf['total_return_pct']:.1f}%")
                    with col2:
                        st.metric("SPY 報酬", f"{benchmark['total_return_pct']:.1f}%")
                    with col3:
                        alpha = perf['total_return_pct'] - benchmark['total_return_pct']
                        st.metric("超額報酬 (Alpha)", f"{alpha:.1f}%",
                                  delta=f"{alpha:.1f}%" if alpha > 0 else None)

                # 權益曲線圖
                st.divider()
                st.subheader("📈 權益曲線")

                equity_data = result["equity_curve"]
                if equity_data:
                    equity_df = pd.DataFrame(equity_data)
                    equity_df["date"] = pd.to_datetime(equity_df["date"])

                    fig = go.Figure()
                    fig.add_trace(go.Scatter(
                        x=equity_df["date"],
                        y=equity_df["value"],
                        mode="lines+markers",
                        name="策略權益",
                        line=dict(color="#2196F3", width=2)
                    ))

                    # 標記週期
                    colors = {"EXPANSION": "green", "PEAK": "orange",
                              "CONTRACTION": "red", "TROUGH": "blue"}
                    for _, row in equity_df.iterrows():
                        fig.add_annotation(
                            x=row["date"],
                            y=row["value"],
                            text=row["phase"][:3],
                            showarrow=False,
                            yshift=10,
                            font=dict(size=8, color=colors.get(row["phase"], "gray"))
                        )

                    fig.update_layout(
                        title="策略權益曲線",
                        xaxis_title="日期",
                        yaxis_title="權益價值",
                        height=400
                    )
                    st.plotly_chart(fig, use_container_width=True)

                # 各週期績效
                st.divider()
                st.subheader("📊 各週期績效")

                phase_perf = result.get("phase_performance", {})
                if phase_perf:
                    phase_data = []
                    for phase, data in phase_perf.items():
                        from config.macro_indicators import MARKET_CYCLES
                        phase_info = MARKET_CYCLES.get(phase, {})
                        phase_data.append({
                            "週期": f"{phase_info.get('emoji', '')} {phase_info.get('name', phase)}",
                            "月數": data["months"],
                            "平均月報酬": f"{data['avg_return']:.2f}%",
                            "累計報酬": f"{data['total_return']:.2f}%"
                        })
                    st.table(pd.DataFrame(phase_data))

                # 週期變化記錄
                st.divider()
                st.subheader("🔄 週期變化記錄")

                cycle_changes = result.get("cycle_changes", [])
                if cycle_changes:
                    for change in cycle_changes:
                        from_phase = change.get("from_phase") or "初始"
                        to_phase = change.get("to_phase")
                        st.markdown(f"**{change['date']}**: {from_phase} → {to_phase} (分數: {change['score']:.2f})")

                # 最近交易記錄
                st.divider()
                st.subheader("📝 近期交易記錄")

                trades = result.get("trades", [])
                if trades:
                    trades_df = pd.DataFrame(trades)
                    trades_df = trades_df[["date", "symbol", "action", "shares", "price", "value", "reason"]]
                    trades_df.columns = ["日期", "股票", "動作", "股數", "價格", "金額", "理由"]
                    st.dataframe(trades_df, use_container_width=True)

            except Exception as e:
                st.error(f"回測失敗: {e}")
                import traceback
                st.code(traceback.format_exc())


def render_sentiment_backtest_page():
    """渲染情緒分析頁面 - 熱門股票、關鍵字、情緒與股價相關性"""
    st.title("📉 新聞情緒分析")
    st.markdown("分析每日熱門股票、討論關鍵字、多空情緒，以及與股價的相關性")

    analyzer = DailyHotStocksAnalyzer()
    backtester = SentimentBacktester()

    # Tab 分頁
    tab1, tab2, tab3, tab4 = st.tabs([
        "🔥 今日熱門股票",
        "📊 熱門關鍵字",
        "📈 情緒vs股價",
        "📋 ETF回測"
    ])

    # ========== Tab 1: 今日熱門股票 ==========
    with tab1:
        st.subheader("🔥 今日熱門討論股票")

        # 日期選擇
        col1, col2 = st.columns([1, 3])
        with col1:
            analysis_date = st.date_input(
                "選擇日期",
                value=date.today() - timedelta(days=1),
                max_value=date.today(),
                key="hot_stocks_date"
            )

        with st.spinner("分析中..."):
            daily_summary = analyzer.get_daily_summary(analysis_date)

        # 整體情緒
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("新聞總數", daily_summary["news_count"])
        with col2:
            st.metric("正面關鍵字", daily_summary.get("positive_count", 0))
        with col3:
            overall = daily_summary.get("overall_sentiment", "無數據")
            st.metric("整體情緒", overall)

        st.divider()

        # 熱門股票表格
        hot_stocks = daily_summary.get("hot_stocks", [])
        if hot_stocks:
            st.markdown("### 📋 討論熱度排行")

            table_data = []
            for stock in hot_stocks[:15]:
                table_data.append({
                    "排名": len(table_data) + 1,
                    "股票": stock["symbol"],
                    "討論次數": stock["mentions"],
                    "看多": stock["bullish"],
                    "看空": stock["bearish"],
                    "情緒": stock["sentiment"],
                    "情緒分數": f"{stock['sentiment_score']:.2f}"
                })

            df = pd.DataFrame(table_data)
            st.dataframe(df, use_container_width=True, hide_index=True)

            # 討論熱度圖
            st.markdown("### 📊 討論熱度分佈")
            fig = go.Figure()

            symbols = [s["symbol"] for s in hot_stocks[:10]]
            mentions = [s["mentions"] for s in hot_stocks[:10]]
            sentiments = [s["sentiment_score"] for s in hot_stocks[:10]]
            colors = ['green' if s > 0.2 else ('red' if s < -0.2 else 'gray') for s in sentiments]

            fig.add_trace(go.Bar(
                x=symbols,
                y=mentions,
                marker_color=colors,
                text=mentions,
                textposition='outside'
            ))

            fig.update_layout(
                xaxis_title="股票",
                yaxis_title="討論次數",
                height=400
            )
            st.plotly_chart(fig, use_container_width=True)

            # 範例新聞標題
            st.markdown("### 📰 熱門股票相關新聞")
            for stock in hot_stocks[:5]:
                if stock.get("sample_titles"):
                    with st.expander(f"**{stock['symbol']}** {stock['sentiment']} ({stock['mentions']} 則)"):
                        for title in stock["sample_titles"]:
                            st.markdown(f"• {title}")
        else:
            st.info("該日無足夠新聞數據")

        # 一週熱門
        st.divider()
        st.markdown("### 📅 本週熱門股票 (過去7天)")

        with st.spinner("分析中..."):
            weekly_hot = analyzer.get_weekly_hot_stocks(analysis_date, days=7)

        if weekly_hot:
            weekly_data = []
            for stock in weekly_hot[:20]:
                weekly_data.append({
                    "股票": stock["symbol"],
                    "總討論次數": stock["total_mentions"],
                    "出現天數": stock["days_mentioned"],
                    "看多": stock["bullish"],
                    "看空": stock["bearish"],
                    "情緒": stock["sentiment"]
                })

            df_weekly = pd.DataFrame(weekly_data)
            st.dataframe(df_weekly, use_container_width=True, hide_index=True)

    # ========== Tab 2: 熱門關鍵字 ==========
    with tab2:
        st.subheader("📊 熱門討論關鍵字")

        col1, col2 = st.columns([1, 3])
        with col1:
            keyword_date = st.date_input(
                "選擇日期",
                value=date.today() - timedelta(days=1),
                max_value=date.today(),
                key="keywords_date"
            )

        with st.spinner("分析中..."):
            daily_summary = analyzer.get_daily_summary(keyword_date)

        trending = daily_summary.get("trending_keywords", [])

        if trending:
            # 關鍵字表格
            kw_data = []
            for kw in trending:
                kw_data.append({
                    "關鍵字": kw["keyword"],
                    "討論次數": kw["mentions"],
                    "正面": kw["bullish"],
                    "負面": kw["bearish"],
                    "情緒": kw["sentiment"]
                })

            df_kw = pd.DataFrame(kw_data)
            st.dataframe(df_kw, use_container_width=True, hide_index=True)

            # 關鍵字雲圖（用柱狀圖代替）
            st.markdown("### 📊 關鍵字熱度")
            fig = go.Figure()

            keywords = [k["keyword"] for k in trending[:12]]
            counts = [k["mentions"] for k in trending[:12]]
            sentiments = [k["sentiment_score"] for k in trending[:12]]
            colors = ['green' if s > 0.2 else ('red' if s < -0.2 else 'orange') for s in sentiments]

            fig.add_trace(go.Bar(
                y=keywords[::-1],
                x=counts[::-1],
                orientation='h',
                marker_color=colors[::-1],
                text=counts[::-1],
                textposition='outside'
            ))

            fig.update_layout(
                xaxis_title="討論次數",
                yaxis_title="關鍵字",
                height=500
            )
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("該日無足夠新聞數據")

    # ========== Tab 3: 情緒 vs 股價相關性 ==========
    with tab3:
        st.subheader("📈 個股情緒 vs 股價相關性分析")

        # 選擇股票
        col1, col2, col3 = st.columns(3)

        with col1:
            # 從 STOCK_KEYWORDS 取得股票列表
            from src.finance.sentiment_backtest import STOCK_KEYWORDS
            stock_options = list(STOCK_KEYWORDS.keys())
            selected_stock = st.selectbox("選擇股票", stock_options, index=0)

        with col2:
            corr_days = st.selectbox(
                "分析期間",
                [30, 60, 90, 180],
                index=2,
                format_func=lambda x: f"{x} 天"
            )

        with col3:
            lead_days = st.selectbox(
                "領先天數",
                [1, 2, 3, 5],
                index=0,
                help="情緒領先股價多少天",
                key="stock_lead_days"
            )

        if st.button("🔍 分析相關性", type="primary"):
            with st.spinner(f"分析 {selected_stock} 情緒與股價相關性..."):
                # 計算該股票的每日情緒
                end_date = date.today()
                start_date = end_date - timedelta(days=corr_days)

                # 取得股票價格
                conn = sqlite3.connect("finance.db")
                price_query = """
                    SELECT date, close
                    FROM daily_prices
                    WHERE symbol = ?
                    AND date BETWEEN ? AND ?
                    ORDER BY date
                """
                price_df = pd.read_sql_query(price_query, conn, params=(
                    selected_stock,
                    start_date.strftime('%Y-%m-%d'),
                    end_date.strftime('%Y-%m-%d')
                ))
                conn.close()

                if price_df.empty:
                    st.warning(f"無法取得 {selected_stock} 的價格數據")
                else:
                    price_df['date'] = pd.to_datetime(price_df['date'])
                    price_df['return_1d'] = price_df['close'].pct_change(1) * 100

                    # 計算該股票的每日情緒
                    news_conn = sqlite3.connect("news.db")
                    keywords = STOCK_KEYWORDS.get(selected_stock, [])
                    keyword_conditions = " OR ".join([
                        f"LOWER(title || ' ' || COALESCE(content, '')) LIKE '%{kw.lower()}%'"
                        for kw in keywords
                    ])

                    sentiment_query = f"""
                        SELECT
                            DATE(COALESCE(
                                CASE WHEN source_type = 'ptt' THEN published_at ELSE collected_at END,
                                collected_at
                            )) as news_date,
                            title,
                            content
                        FROM news
                        WHERE DATE(COALESCE(
                            CASE WHEN source_type = 'ptt' THEN published_at ELSE collected_at END,
                            collected_at
                        )) BETWEEN ? AND ?
                        AND ({keyword_conditions})
                    """

                    news_df = pd.read_sql_query(sentiment_query, news_conn, params=(
                        start_date.strftime('%Y-%m-%d'),
                        end_date.strftime('%Y-%m-%d')
                    ))
                    news_conn.close()

                    if news_df.empty:
                        st.warning(f"無法取得 {selected_stock} 的新聞數據")
                    else:
                        # 計算每日情緒
                        from src.finance.sentiment_backtest import POSITIVE_KEYWORDS, NEGATIVE_KEYWORDS

                        daily_sentiment = []
                        for news_date, group in news_df.groupby('news_date'):
                            text_all = " ".join([
                                (str(row['title']) + " " + str(row['content'] or "")).lower()
                                for _, row in group.iterrows()
                            ])
                            pos = sum(1 for kw in POSITIVE_KEYWORDS if kw in text_all)
                            neg = sum(1 for kw in NEGATIVE_KEYWORDS if kw in text_all)
                            total = pos + neg
                            score = (pos - neg) / total if total > 0 else 0

                            daily_sentiment.append({
                                'date': news_date,
                                'mentions': len(group),
                                'sentiment_score': score,
                                'bullish': pos,
                                'bearish': neg
                            })

                        sentiment_df = pd.DataFrame(daily_sentiment)
                        sentiment_df['date'] = pd.to_datetime(sentiment_df['date'])

                        # 合併數據
                        merged = pd.merge(sentiment_df, price_df, on='date', how='inner')

                        if len(merged) < 10:
                            st.warning("數據點不足，無法進行有效分析")
                        else:
                            # 計算相關性
                            merged['sentiment_lagged'] = merged['sentiment_score'].shift(lead_days)
                            merged['mentions_lagged'] = merged['mentions'].shift(lead_days)
                            analysis_df = merged.dropna()

                            if len(analysis_df) > 5:
                                corr_sentiment = analysis_df['sentiment_lagged'].corr(analysis_df['return_1d'])
                                corr_mentions = analysis_df['mentions_lagged'].corr(analysis_df['return_1d'])

                                # 顯示結果
                                st.success(f"✅ 分析完成！共 {len(analysis_df)} 個數據點")

                                col1, col2, col3 = st.columns(3)
                                with col1:
                                    st.metric(
                                        "情緒-報酬相關性",
                                        f"{corr_sentiment:.4f}",
                                        help="正值表示情緒正面時股價傾向上漲"
                                    )
                                with col2:
                                    st.metric(
                                        "討論量-報酬相關性",
                                        f"{corr_mentions:.4f}",
                                        help="正值表示討論增加時股價傾向上漲"
                                    )
                                with col3:
                                    avg_mentions = analysis_df['mentions'].mean()
                                    st.metric("平均每日討論", f"{avg_mentions:.1f} 則")

                                # 結論
                                st.divider()
                                st.markdown("### 📝 分析結論")

                                if abs(corr_sentiment) > 0.15:
                                    st.success(f"✅ {selected_stock} 的新聞情緒與股價有較強相關性 ({corr_sentiment:.3f})")
                                elif abs(corr_sentiment) > 0.08:
                                    st.warning(f"⚠️ {selected_stock} 的新聞情緒與股價有弱相關性 ({corr_sentiment:.3f})")
                                else:
                                    st.info(f"ℹ️ {selected_stock} 的新聞情緒與股價幾乎無相關 ({corr_sentiment:.3f})")

                                if corr_mentions > 0.1:
                                    st.info("💡 討論量增加時，股價傾向上漲")
                                elif corr_mentions < -0.1:
                                    st.info("💡 討論量增加時，股價傾向下跌（可能是利空消息）")

                                # 走勢圖
                                st.divider()
                                st.markdown("### 📊 情緒 vs 股價走勢")

                                fig = make_subplots(
                                    rows=3, cols=1,
                                    shared_xaxes=True,
                                    vertical_spacing=0.08,
                                    row_heights=[0.4, 0.3, 0.3],
                                    subplot_titles=(f"{selected_stock} 股價", "新聞情緒", "討論次數")
                                )

                                # 股價
                                fig.add_trace(
                                    go.Scatter(x=merged['date'], y=merged['close'],
                                              name="股價", line=dict(color='#1f77b4', width=2)),
                                    row=1, col=1
                                )

                                # 情緒
                                colors = ['green' if s > 0 else 'red' for s in merged['sentiment_score']]
                                fig.add_trace(
                                    go.Bar(x=merged['date'], y=merged['sentiment_score'],
                                          name="情緒", marker_color=colors, opacity=0.7),
                                    row=2, col=1
                                )
                                fig.add_hline(y=0, line_dash="dash", line_color="gray", row=2, col=1)

                                # 討論量
                                fig.add_trace(
                                    go.Bar(x=merged['date'], y=merged['mentions'],
                                          name="討論次數", marker_color='orange', opacity=0.7),
                                    row=3, col=1
                                )

                                fig.update_layout(height=700, showlegend=False)
                                st.plotly_chart(fig, use_container_width=True)

    # ========== Tab 4: ETF 回測 ==========
    with tab4:
        st.subheader("📋 整體市場情緒 vs ETF 回測")

        col1, col2 = st.columns(2)
        with col1:
            lookback_days = st.selectbox(
                "回測期間",
                [30, 90, 180, 365],
                index=2,
                format_func=lambda x: f"{x} 天",
                key="etf_lookback"
            )
        with col2:
            etf_options = ["SPY", "QQQ", "DIA", "IWM", "VGT", "XLF", "XLE", "XLV"]
            selected_etf = st.selectbox("選擇 ETF", etf_options, index=0, key="etf_select")

        end_date = date.today()
        start_date = end_date - timedelta(days=lookback_days)

        with st.spinner("執行回測..."):
            result = backtester.run_backtest(
                etf_symbol=selected_etf,
                start_date=start_date,
                end_date=end_date,
                lead_days=1
            )

        if "error" in result:
            st.error(result["error"])
        else:
            col1, col2, col3, col4 = st.columns(4)
            with col1:
                st.metric("相關係數", f"{result['correlation']:.4f}")
            with col2:
                st.metric("整體勝率", f"{result['win_rate']['overall']:.1f}%")
            with col3:
                st.metric("情緒正→漲", f"{result['win_rate']['positive_sentiment_up']:.1f}%")
            with col4:
                st.metric("情緒負→跌", f"{result['win_rate']['negative_sentiment_down']:.1f}%")

            # 多ETF比較
            st.divider()
            st.markdown("### 📊 多 ETF 比較")

            with st.spinner("比較中..."):
                results = backtester.run_multi_etf_backtest(
                    etf_symbols=etf_options,
                    start_date=start_date,
                    end_date=end_date
                )

            if results:
                comparison_data = [{
                    "ETF": r["etf_symbol"],
                    "相關係數": f"{r['correlation']:.4f}",
                    "勝率": f"{r['win_rate']['overall']:.1f}%",
                    "正→漲": f"{r['win_rate']['positive_sentiment_up']:.1f}%"
                } for r in results]

                st.dataframe(pd.DataFrame(comparison_data), use_container_width=True, hide_index=True)


# ========== 側邊欄 ==========
st.sidebar.title("📈 股票與新聞分析")
st.sidebar.markdown("---")

# Supabase 模式提示
if USE_SUPABASE:
    st.sidebar.success("☁️ **Supabase 雲端資料庫**")
elif DEMO_MODE:
    st.sidebar.info("📌 **示範模式**\n使用有限的示範數據")
    st.toast("正在使用示範資料庫，數據有限", icon="ℹ️")

# 檢查資料庫是否存在
db_exists = DB_PATH.exists() or USE_SUPABASE
finance_db_exists = FINANCE_DB_PATH.exists() or USE_SUPABASE

if not db_exists and not finance_db_exists:
    st.error("⚠️ 資料庫檔案不存在")
    st.info("""
    **這是一個股票新聞分析系統，需要本地資料庫才能運行。**

    請在本地環境執行以下步驟：

    1. 安裝套件：`pip install -r requirements.txt`
    2. 初始化新聞收集：`python main.py`
    3. 初始化股票數據：`python finance_collector.py --init --fast`
    4. 啟動應用：`streamlit run app.py`

    **GitHub**: https://github.com/manibari/news
    """)
    st.stop()

st.sidebar.subheader("📅 選擇日期")

# 安全取得可用日期
if USE_SUPABASE or db_exists:
    try:
        available_dates = get_available_dates()
    except Exception as e:
        available_dates = []
else:
    available_dates = []

if available_dates:
    min_date = min(available_dates)
    max_date = max(available_dates)

    # 初始化 session_state 中的日期
    if "selected_date" not in st.session_state:
        st.session_state.selected_date = max_date

    # 快速選擇按鈕的回調函數
    def set_today():
        st.session_state.selected_date = date.today()

    def set_yesterday():
        st.session_state.selected_date = date.today() - timedelta(days=1)

    # 日期選擇器（使用 session_state）
    selected_date = st.sidebar.date_input(
        "日期",
        value=st.session_state.selected_date,
        min_value=min_date,
        max_value=max_date,
        format="YYYY-MM-DD",
        key="date_picker"
    )
    # 同步更新 session_state
    st.session_state.selected_date = selected_date

    st.sidebar.markdown("**快速選擇:**")
    col1, col2 = st.sidebar.columns(2)
    with col1:
        st.button("今天", use_container_width=True, on_click=set_today)
    with col2:
        st.button("昨天", use_container_width=True, on_click=set_yesterday)

    # 使用 session_state 的值
    selected_date = st.session_state.selected_date

    st.sidebar.markdown(f"*資料範圍: {min_date} ~ {max_date}*")
else:
    st.sidebar.warning("資料庫中沒有新聞")
    selected_date = date.today()

    # 顯示如何收集新聞的說明
    st.warning("⚠️ 資料庫中沒有新聞數據")
    st.info("""
    **請先執行新聞收集：**

    ```bash
    # 收集今日新聞
    python main.py

    # 收集 PTT 歷史文章 (過去一年)
    python collect_ptt_historical.py --pages 500

    # 收集股票數據
    python collect_stock_historical.py
    ```

    收集完成後重新整理頁面即可。
    """)

st.sidebar.markdown("---")

# ========== 新聞篩選設定 ==========
st.sidebar.subheader("🔍 新聞篩選")

# PTT 最低推文數
if "ptt_min_push" not in st.session_state:
    st.session_state.ptt_min_push = 30

ptt_min_push = st.sidebar.slider(
    "PTT 最低推文數",
    min_value=0,
    max_value=100,
    value=st.session_state.ptt_min_push,
    step=10,
    help="只顯示推文數 >= 此值的 PTT 文章"
)
st.session_state.ptt_min_push = ptt_min_push

# 排除社論
if "exclude_editorial" not in st.session_state:
    st.session_state.exclude_editorial = True

exclude_editorial = st.sidebar.checkbox(
    "排除社論/評論",
    value=st.session_state.exclude_editorial,
    help="過濾掉個人評論、社論、專欄類文章"
)
st.session_state.exclude_editorial = exclude_editorial

st.sidebar.markdown("---")

# 市場週期燈號
try:
    _macro_db = MacroDatabase()
    _latest_cycle = _macro_db.get_latest_market_cycle()
    if _latest_cycle:
        from config.macro_indicators import MARKET_CYCLES
        _phase = _latest_cycle.get("phase", "")
        _phase_info = MARKET_CYCLES.get(_phase, {})
        _phase_name = _phase_info.get("name", _phase)
        _phase_emoji = _phase_info.get("emoji", "")
        _phase_color = _phase_info.get("color", "#888888")
        st.sidebar.markdown("**市場週期:**")
        st.sidebar.markdown(f"""
        <div style="background-color: {_phase_color}; padding: 10px; border-radius: 8px; text-align: center; margin-bottom: 10px;">
            <span style="color: white; font-weight: bold;">{_phase_emoji} {_phase_name}</span>
        </div>
        """, unsafe_allow_html=True)
except:
    pass

st.sidebar.markdown("---")

# 燈號說明
st.sidebar.markdown("**燈號說明:**")
st.sidebar.markdown("🟢 正面趨勢")
st.sidebar.markdown("🟡 中性/觀望")
st.sidebar.markdown("🔴 負面趨勢")

st.sidebar.markdown("---")

page = st.sidebar.radio(
    "選擇頁面",
    ["📊 新聞總結", "🎯 趨勢雷達", "💰 季度回測", "🔬 個股分析", "📈 股票數據", "🎯 交易分析", "🌍 總經分析", "📉 情緒回測", "📋 股票清單", "📰 新聞列表", "🇹🇼 PTT Stock"],
    index=0
)

st.sidebar.markdown("---")
st.sidebar.markdown(f"**更新時間**: {datetime.now().strftime('%H:%M:%S')}")

if st.sidebar.button("🔄 重新整理", use_container_width=True):
    st.cache_resource.clear()
    st.rerun()

# ========== 頁面路由 ==========
if page == "📊 新聞總結":
    render_summary_page(selected_date)
elif page == "🎯 趨勢雷達":
    render_trend_radar_page()
elif page == "💰 季度回測":
    render_quarterly_backtest_page()
elif page == "🔬 個股分析":
    render_individual_stock_page(selected_date)
elif page == "📈 股票數據":
    render_stock_page(selected_date)
elif page == "🎯 交易分析":
    render_analysis_page()
elif page == "🌍 總經分析":
    render_macro_analysis_page()
elif page == "📉 情緒回測":
    render_sentiment_backtest_page()
elif page == "📋 股票清單":
    render_watchlist_page()
elif page == "📰 新聞列表":
    render_news_list_page(selected_date)
elif page == "🇹🇼 PTT Stock":
    render_ptt_page(selected_date)
