"""
新聞瀏覽 & 股票數據 Streamlit 應用程式
"""

import sqlite3
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

# 頁面設定
st.set_page_config(
    page_title="股票數據與新聞分析",
    page_icon="📈",
    layout="wide",
)

# 資料庫路徑
DB_PATH = Path(__file__).parent / "news.db"
FINANCE_DB_PATH = Path(__file__).parent / "finance.db"

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
    "outperform", "upbeat", "confident", "improve", "advance", "climb"
]

NEGATIVE_KEYWORDS = [
    "plunge", "crash", "fall", "drop", "decline", "slump", "tumble", "sink", "lose",
    "loss", "cut", "layoff", "fire", "recession", "crisis", "fear", "worry", "concern",
    "pessimis", "bearish", "downgrade", "weak", "miss", "disappoint", "warn", "threat",
    "risk", "uncertain", "volatile", "trouble", "struggle", "fail", "worse than expected",
    "shutdown", "default", "bankruptcy"
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

    # 總經類別使用專屬模板

    # Fed/利率
    if category == "Fed/利率":
        if "hold" in text_all or "steady" in text_all or "unchanged" in text_all:
            return "Fed 預期維持利率不變，市場觀望態度濃厚"
        elif "cut" in text_all or "lower" in text_all:
            return "市場預期降息，風險資產可能受惠"
        elif "hike" in text_all or "raise" in text_all:
            return "升息預期升溫，債券殖利率走高"
        else:
            return f"Fed 政策動向受關注：{top_news[:50]}..."

    # 通膨
    elif category == "通膨":
        if "ease" in text_all or "cool" in text_all or "slow" in text_all:
            return "通膨降溫跡象，有利於寬鬆政策"
        elif "rise" in text_all or "surge" in text_all or "hot" in text_all:
            return "通膨壓力升溫，可能延後降息時程"
        else:
            return f"通膨動態：{top_news[:50]}..."

    # 就業
    elif category == "就業":
        if "strong" in text_all or "add" in text_all or "hire" in text_all:
            return "就業市場強勁，經濟基本面穩健"
        elif "layoff" in text_all or "cut" in text_all or "job" in text_all and "loss" in text_all:
            return "裁員消息頻傳，就業市場出現壓力"
        else:
            return f"就業市場動態：{top_news[:50]}..."

    # 美元/匯率
    elif category == "美元/匯率":
        if "weak" in text_all or "fall" in text_all or "drop" in text_all or "slip" in text_all:
            return "美元走弱，新興市場與大宗商品受惠"
        elif "strong" in text_all or "rise" in text_all or "surge" in text_all:
            return "美元走強，出口與新興市場承壓"
        elif "intervention" in text_all:
            return "匯市干預預期升溫，波動加劇"
        else:
            return f"匯率動態：{top_news[:50]}..."

    # 黃金/避險
    elif category == "黃金/避險":
        if "record" in text_all or "high" in text_all or "surge" in text_all:
            return "黃金創新高，避險需求強勁"
        elif "fall" in text_all or "drop" in text_all:
            return "黃金回落，風險偏好回升"
        else:
            return f"貴金屬動態：{top_news[:50]}..."

    # 貿易/關稅
    elif category == "貿易/關稅":
        if "tariff" in text_all and ("raise" in text_all or "impose" in text_all or "threat" in text_all):
            return "關稅威脅升級，貿易摩擦風險上升"
        elif "deal" in text_all or "agreement" in text_all:
            return "貿易協議進展，市場情緒改善"
        else:
            return f"貿易動態：{top_news[:50]}..."

    # 政府政策
    elif category == "政府政策":
        if "shutdown" in text_all:
            return "政府關門風險升高，市場不確定性增加"
        elif "stimulus" in text_all or "spending" in text_all:
            return "財政刺激政策動向，關注經濟影響"
        else:
            return f"政策動態：{top_news[:50]}..."

    # 債券
    elif category == "債券/殖利率":
        if "rise" in text_all or "surge" in text_all or "climb" in text_all:
            return "殖利率上升，債券價格承壓"
        elif "fall" in text_all or "drop" in text_all:
            return "殖利率下滑，資金流向避險資產"
        else:
            return f"債市動態：{top_news[:50]}..."

    # GDP/經濟成長
    elif category == "GDP/經濟成長":
        if "recession" in text_all or "contract" in text_all:
            return "經濟衰退疑慮升溫，防禦性資產受青睞"
        elif "growth" in text_all or "expand" in text_all:
            return "經濟成長穩健，支撐企業獲利預期"
        else:
            return f"經濟動態：{top_news[:50]}..."

    # 科技/AI
    elif category == "科技/AI":
        if "spend" in text_all or "invest" in text_all:
            return "AI 投資熱潮持續，科技股受關注"
        elif "layoff" in text_all or "cut" in text_all:
            return "科技業裁員消息頻傳，成本控管為重點"
        elif "earn" in text_all:
            return "科技巨頭財報週，AI 支出成焦點"
        else:
            return f"科技動態：{top_news[:50]}..."

    # 醫療保健
    elif category == "醫療保健":
        if "plunge" in text_all or "drop" in text_all or "fall" in text_all:
            return "醫療股重挫，政策風險衝擊估值"
        elif "fda" in text_all or "approv" in text_all:
            return "FDA 審批動態，藥廠股價波動"
        else:
            return f"醫療動態：{top_news[:50]}..."

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
            return f"汽車產業：{top_news[:50]}..."

    # 航空/運輸
    elif category == "航空/運輸":
        if "layoff" in text_all or "cut" in text_all:
            return "物流業調整人力，反映需求變化"
        elif "earn" in text_all:
            return "運輸業財報公布，關注營運展望"
        else:
            return f"運輸動態：{top_news[:50]}..."

    # 金融/銀行
    elif category == "金融/銀行":
        if "earn" in text_all:
            return "銀行財報季，關注淨利差與信貸品質"
        else:
            return f"金融動態：{top_news[:50]}..."

    # 能源
    elif category == "能源":
        if "oil" in text_all and ("rise" in text_all or "surge" in text_all):
            return "油價上漲，能源股受惠"
        elif "oil" in text_all and ("fall" in text_all or "drop" in text_all):
            return "油價下跌，通膨壓力緩解"
        else:
            return f"能源動態：{top_news[:50]}..."

    # 零售/消費
    elif category == "零售/消費":
        if "spend" in text_all and ("strong" in text_all or "rise" in text_all):
            return "消費支出強勁，零售股表現可期"
        elif "weak" in text_all or "slow" in text_all:
            return "消費動能放緩，零售業承壓"
        else:
            return f"零售動態：{top_news[:50]}..."

    # 房地產
    elif category == "房地產":
        if "mortgage" in text_all and "rate" in text_all:
            return "房貸利率變動，影響購屋需求"
        else:
            return f"房市動態：{top_news[:50]}..."

    # 加密貨幣
    elif category == "加密貨幣":
        if "surge" in text_all or "rally" in text_all or "rise" in text_all:
            return "加密貨幣上漲，市場風險偏好回升"
        elif "fall" in text_all or "drop" in text_all:
            return "加密貨幣回落，投資人轉趨保守"
        else:
            return f"加密貨幣：{top_news[:50]}..."

    # 預設
    return f"{top_news[:60]}..."


@st.cache_resource
def get_connection():
    """取得資料庫連接"""
    return sqlite3.connect(DB_PATH, check_same_thread=False)


@st.cache_resource
def get_finance_connection():
    """取得金融資料庫連接"""
    return sqlite3.connect(FINANCE_DB_PATH, check_same_thread=False)


def get_watchlist():
    """取得追蹤清單"""
    conn = get_finance_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT symbol, name, market, sector, industry, description
        FROM watchlist
        WHERE is_active = 1
        ORDER BY market, symbol
    """)
    columns = ["symbol", "name", "market", "sector", "industry", "description"]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def get_stock_info(symbol: str):
    """取得單一股票的詳細資訊"""
    conn = get_finance_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT symbol, name, market, sector, industry, description
        FROM watchlist
        WHERE symbol = ?
    """, (symbol,))
    row = cursor.fetchone()
    if row:
        columns = ["symbol", "name", "market", "sector", "industry", "description"]
        return dict(zip(columns, row))
    return None


def get_stock_prices(symbol: str, start_date: date = None, end_date: date = None):
    """取得股票價格數據"""
    conn = get_finance_connection()
    cursor = conn.cursor()

    query = """
        SELECT date, open, high, low, close, volume
        FROM daily_prices
        WHERE symbol = ?
    """
    params = [symbol]

    if start_date:
        query += " AND date >= ?"
        params.append(start_date.strftime("%Y-%m-%d"))
    if end_date:
        query += " AND date <= ?"
        params.append(end_date.strftime("%Y-%m-%d"))

    query += " ORDER BY date ASC"
    cursor.execute(query, params)

    columns = ["date", "open", "high", "low", "close", "volume"]
    data = [dict(zip(columns, row)) for row in cursor.fetchall()]

    # 轉換為 DataFrame
    if data:
        df = pd.DataFrame(data)
        df["date"] = pd.to_datetime(df["date"])
        return df
    return pd.DataFrame()


def get_stock_fundamentals(symbol: str):
    """取得股票基本面數據"""
    conn = get_finance_connection()
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
    """取得與股票相關的新聞"""
    conn = get_connection()
    cursor = conn.cursor()

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

    # 搜尋新聞（PTT 用 published_at，其他用 collected_at）
    date_str = selected_date.strftime("%Y-%m-%d")
    all_news = []

    for keyword in keywords:
        cursor.execute("""
            SELECT id, title, content, url, source, category, published_at
            FROM news
            WHERE ((source_type != 'ptt' AND date(collected_at) = ?)
                   OR (source_type = 'ptt' AND date(published_at) = ?))
            AND (LOWER(title) LIKE ? OR LOWER(content) LIKE ?)
            ORDER BY published_at DESC
        """, (date_str, date_str, f"%{keyword}%", f"%{keyword}%"))

        columns = ["id", "title", "content", "url", "source", "category", "published_at"]
        news = [dict(zip(columns, row)) for row in cursor.fetchall()]
        all_news.extend(news)

    # 去重
    seen_ids = set()
    unique_news = []
    for n in all_news:
        if n["id"] not in seen_ids:
            seen_ids.add(n["id"])
            unique_news.append(n)

    return unique_news


def get_news_in_date_range(start_date: date, end_date: date, keyword: str = None):
    """取得日期範圍內的新聞（PTT 用 published_at，其他用 collected_at）"""
    conn = get_connection()
    cursor = conn.cursor()

    start_str = start_date.strftime("%Y-%m-%d")
    end_str = end_date.strftime("%Y-%m-%d")

    # 使用 CASE 來根據 source_type 選擇正確的日期欄位
    query = """
        SELECT news_date, COUNT(*) as count FROM (
            SELECT CASE
                WHEN source_type = 'ptt' THEN date(published_at)
                ELSE date(collected_at)
            END as news_date, title, content
            FROM news
            WHERE (source_type != 'ptt' AND date(collected_at) BETWEEN ? AND ?)
               OR (source_type = 'ptt' AND date(published_at) BETWEEN ? AND ?)
        )
    """
    params = [start_str, end_str, start_str, end_str]

    if keyword:
        query += " WHERE (LOWER(title) LIKE ? OR LOWER(content) LIKE ?)"
        params.extend([f"%{keyword.lower()}%", f"%{keyword.lower()}%"])

    query += " GROUP BY news_date ORDER BY news_date"

    cursor.execute(query, params)
    return {row[0]: row[1] for row in cursor.fetchall()}


def get_available_dates():
    """取得有新聞的日期列表（整合 collected_at 和 published_at）"""
    conn = get_connection()
    cursor = conn.cursor()
    # 對非 PTT 來源使用 collected_at，對 PTT 使用 published_at
    cursor.execute("""
        SELECT DISTINCT news_date FROM (
            SELECT date(collected_at) as news_date FROM news WHERE source_type != 'ptt'
            UNION
            SELECT date(published_at) as news_date FROM news WHERE source_type = 'ptt'
        )
        ORDER BY news_date DESC
    """)
    dates = [row[0] for row in cursor.fetchall()]
    return [datetime.strptime(d, "%Y-%m-%d").date() for d in dates if d]


def get_ptt_available_dates():
    """取得 PTT 有文章的日期列表"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT DISTINCT date(published_at) as news_date
        FROM news
        WHERE source_type = 'ptt' AND published_at IS NOT NULL
        ORDER BY news_date DESC
    """)
    dates = [row[0] for row in cursor.fetchall()]
    return [datetime.strptime(d, "%Y-%m-%d").date() for d in dates if d]


def get_news_by_date(selected_date: date):
    """取得指定日期的新聞（PTT 用 published_at，其他用 collected_at）"""
    conn = get_connection()
    cursor = conn.cursor()
    date_str = selected_date.strftime("%Y-%m-%d")
    # 對 PTT 使用 published_at，其他來源使用 collected_at
    cursor.execute("""
        SELECT id, title, content, url, source, category, source_type,
               published_at, collected_at
        FROM news
        WHERE (source_type != 'ptt' AND date(collected_at) = ?)
           OR (source_type = 'ptt' AND date(published_at) = ?)
        ORDER BY COALESCE(published_at, collected_at) DESC
    """, (date_str, date_str))
    columns = ["id", "title", "content", "url", "source", "category",
               "source_type", "published_at", "collected_at"]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def get_news_stats_by_date(selected_date: date):
    """取得指定日期的新聞統計（PTT 用 published_at，其他用 collected_at）"""
    conn = get_connection()
    cursor = conn.cursor()
    date_str = selected_date.strftime("%Y-%m-%d")

    # 條件：PTT 用 published_at，其他用 collected_at
    date_condition = """
        ((source_type != 'ptt' AND date(collected_at) = ?)
         OR (source_type = 'ptt' AND date(published_at) = ?))
    """

    cursor.execute(f"SELECT COUNT(*) FROM news WHERE {date_condition}", (date_str, date_str))
    total_count = cursor.fetchone()[0]

    cursor.execute(f"""
        SELECT source_type, COUNT(*) FROM news
        WHERE {date_condition} GROUP BY source_type
    """, (date_str, date_str))
    by_source_type = dict(cursor.fetchall())

    cursor.execute(f"""
        SELECT source, COUNT(*) FROM news
        WHERE {date_condition}
        GROUP BY source ORDER BY COUNT(*) DESC LIMIT 10
    """, (date_str, date_str))
    by_source = dict(cursor.fetchall())

    return {
        "total_count": total_count,
        "by_source_type": by_source_type,
        "by_source": by_source,
    }


def get_weekly_news(end_date: date, days: int = 7) -> list:
    """取得過去一週的新聞"""
    conn = get_connection()
    cursor = conn.cursor()
    start_date = end_date - timedelta(days=days)
    start_str = start_date.strftime("%Y-%m-%d")
    end_str = end_date.strftime("%Y-%m-%d")

    cursor.execute("""
        SELECT id, title, content, url, source, category, source_type,
               published_at, collected_at
        FROM news
        WHERE (source_type != 'ptt' AND date(collected_at) BETWEEN ? AND ?)
           OR (source_type = 'ptt' AND date(published_at) BETWEEN ? AND ?)
        ORDER BY COALESCE(published_at, collected_at) DESC
    """, (start_str, end_str, start_str, end_str))

    columns = ["id", "title", "content", "url", "source", "category",
               "source_type", "published_at", "collected_at"]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


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

    news_list = get_news_by_date(selected_date)
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

    # 總覽表格 - 固定順序
    st.markdown("#### 快速總覽")
    overview_data = []
    for category in MACRO_KEYWORDS.keys():
        news_items = macro_news.get(category, [])
        if news_items:
            light, _ = analyze_sentiment(news_items)
            summary = generate_summary(category, news_items, light)
        else:
            light = "⚪"  # 無資料用灰色
            summary = "今日無相關新聞"
        overview_data.append({
            "燈號": light,
            "分類": category,
            "總結": summary,
            "新聞數": len(news_items)
        })

    df_overview = pd.DataFrame(overview_data)
    st.dataframe(df_overview, use_container_width=True, hide_index=True)

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

    # 取得過去一週新聞用於趨勢分析
    weekly_news_list = get_weekly_news(selected_date, days=7)
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
    st.dataframe(df_overview, use_container_width=True, hide_index=True)

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
    st.dataframe(df_overview, use_container_width=True, hide_index=True)

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

    news_list = get_news_by_date(selected_date)

    if not news_list:
        st.warning(f"{selected_date} 沒有收集到新聞")
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
    """取得指定日期的 PTT 文章"""
    conn = get_connection()
    cursor = conn.cursor()
    date_str = selected_date.strftime("%Y-%m-%d")
    cursor.execute("""
        SELECT id, title, content, url, source, category, source_type,
               published_at, collected_at
        FROM news
        WHERE date(published_at) = ? AND source_type = 'ptt'
        ORDER BY published_at DESC
    """, (date_str,))
    columns = ["id", "title", "content", "url", "source", "category",
               "source_type", "published_at", "collected_at"]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def render_ptt_page(selected_date: date):
    """渲染 PTT Stock 頁面"""
    st.title("🇹🇼 PTT Stock 版")
    st.markdown(f"**日期**: {selected_date.strftime('%Y-%m-%d')}")

    ptt_news = get_ptt_news_by_date(selected_date)

    if not ptt_news:
        st.warning(f"{selected_date} 沒有 PTT 文章")
        st.info("提示：執行 `python main.py --once` 來收集 PTT 文章")
        return

    # 統計
    categories = {}
    for news in ptt_news:
        cat = news["category"] or "其他"
        categories[cat] = categories.get(cat, 0) + 1

    # 顯示統計
    st.markdown(f"共 **{len(ptt_news)}** 則文章")

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

st.sidebar.subheader("📅 選擇日期")

available_dates = get_available_dates()

if available_dates:
    min_date = min(available_dates)
    max_date = max(available_dates)

    selected_date = st.sidebar.date_input(
        "日期",
        value=max_date,
        min_value=min_date,
        max_value=max_date,
        format="YYYY-MM-DD"
    )

    st.sidebar.markdown("**快速選擇:**")
    col1, col2 = st.sidebar.columns(2)
    with col1:
        if st.button("今天", use_container_width=True):
            selected_date = date.today()
    with col2:
        if st.button("昨天", use_container_width=True):
            selected_date = date.today() - timedelta(days=1)

    st.sidebar.markdown(f"*資料範圍: {min_date} ~ {max_date}*")
else:
    st.sidebar.warning("資料庫中沒有新聞")
    selected_date = date.today()

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
    ["📊 新聞總結", "📈 股票數據", "🎯 交易分析", "🌍 總經分析", "📉 情緒回測", "📋 股票清單", "📰 新聞列表", "🇹🇼 PTT Stock"],
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
