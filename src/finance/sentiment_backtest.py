"""
新聞情緒 vs ETF 回測分析模組

分析新聞情緒是否能作為 ETF 大盤的領先指標
"""

import sqlite3
import logging
from datetime import datetime, timedelta, date
from typing import Dict, List, Optional, Tuple
import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)

# 正面關鍵字
POSITIVE_KEYWORDS = [
    "surge", "soar", "jump", "rally", "gain", "rise", "climb", "beat",
    "record high", "all-time high", "bullish", "optimistic", "strong",
    "growth", "profit", "upgrade", "buy", "outperform", "exceed",
    "漲", "飆", "創新高", "利多", "看好", "成長", "獲利", "優於預期",
    "突破", "反彈", "回升", "熱絡", "強勢", "樂觀"
]

# 負面關鍵字
NEGATIVE_KEYWORDS = [
    "plunge", "crash", "tumble", "drop", "fall", "decline", "sink",
    "miss", "cut", "layoff", "bearish", "pessimistic", "weak",
    "loss", "downgrade", "sell", "underperform", "disappoint",
    "recession", "crisis", "fear", "panic", "warning", "risk",
    "跌", "崩", "暴跌", "利空", "看壞", "衰退", "虧損", "不如預期",
    "下修", "疲軟", "萎縮", "悲觀", "警示", "風險"
]

# 產業對應的 ETF
SECTOR_ETF_MAPPING = {
    "半導體": ["SMH", "SOXX", "VGT", "QQQ"],
    "軟體/雲端": ["IGV", "WCLD", "VGT", "QQQ"],
    "網路/社群": ["SOCL", "QQQ", "VGT"],
    "AI人工智慧": ["BOTZ", "ROBO", "QQQ", "VGT"],
    "金融": ["XLF", "KBE", "KRE"],
    "醫療保健": ["XLV", "IBB", "XBI"],
    "能源": ["XLE", "OIH", "XOP"],
    "汽車": ["CARZ", "DRIV", "QQQ"],
    "零售/消費": ["XRT", "XLY", "VCR"],
    "工業": ["XLI", "IYJ"],
    "公用事業": ["XLU", "VPU"],
    "基礎材料": ["XLB", "VAW"],
    "鋼鐵/石化/水泥": ["SLX", "XLB"],
    "通訊服務": ["XLC", "VOX"],
    "房地產": ["VNQ", "IYR", "XLRE"],
    "加密貨幣": ["BITO", "GBTC"],
    # 大盤
    "整體市場": ["SPY", "QQQ", "DIA", "IWM"],
    "科技": ["VGT", "XLK", "QQQ"],
}

# 主要追蹤的 ETF（有價格數據的）
PRIMARY_ETFS = ["SPY", "QQQ", "VGT", "XLF", "XLE", "XLV", "DIA", "IWM", "GLD", "TLT"]

# 股票關鍵字對照表（用於識別新聞中提到的股票）
STOCK_KEYWORDS = {
    # 美股科技巨頭
    "NVDA": ["nvidia", "nvda", "輝達", "黃仁勳"],
    "AAPL": ["apple", "aapl", "蘋果", "iphone", "ipad", "mac"],
    "MSFT": ["microsoft", "msft", "微軟", "azure", "windows"],
    "GOOGL": ["google", "googl", "alphabet", "谷歌", "youtube"],
    "META": ["meta", "facebook", "fb", "臉書", "instagram", "whatsapp"],
    "AMZN": ["amazon", "amzn", "亞馬遜", "aws"],
    "TSLA": ["tesla", "tsla", "特斯拉", "馬斯克", "musk"],
    # 半導體
    "AMD": ["amd", "超微"],
    "INTC": ["intel", "intc", "英特爾"],
    "AVGO": ["broadcom", "avgo", "博通"],
    "QCOM": ["qualcomm", "qcom", "高通"],
    "MU": ["micron", "mu", "美光"],
    "ASML": ["asml", "艾司摩爾"],
    "TSM": ["tsmc", "台積電", "2330"],
    "SMCI": ["supermicro", "smci", "超微電腦"],
    # AI/SaaS
    "PLTR": ["palantir", "pltr"],
    "CRM": ["salesforce", "crm"],
    "SNOW": ["snowflake", "snow"],
    "CRWD": ["crowdstrike", "crwd"],
    "DDOG": ["datadog", "ddog"],
    "MDB": ["mongodb", "mdb"],
    # 金融
    "JPM": ["jpmorgan", "jpm", "摩根大通"],
    "GS": ["goldman", "gs", "高盛"],
    "MS": ["morgan stanley", "ms", "摩根士丹利"],
    "BAC": ["bank of america", "bac", "美銀"],
    "V": ["visa"],
    "MA": ["mastercard", "ma"],
    # 醫療
    "UNH": ["unitedhealth", "unh"],
    "LLY": ["eli lilly", "lly", "禮來"],
    "NVO": ["novo nordisk", "nvo", "諾和諾德"],
    "PFE": ["pfizer", "pfe", "輝瑞"],
    "MRNA": ["moderna", "mrna", "莫德納"],
    # 能源
    "XOM": ["exxon", "xom", "埃克森"],
    "CVX": ["chevron", "cvx", "雪佛龍"],
    # 消費
    "WMT": ["walmart", "wmt", "沃爾瑪"],
    "COST": ["costco", "cost", "好市多"],
    "NKE": ["nike", "nke", "耐吉"],
    "SBUX": ["starbucks", "sbux", "星巴克"],
    # 其他
    "NFLX": ["netflix", "nflx", "網飛"],
    "DIS": ["disney", "dis", "迪士尼"],
    "BA": ["boeing", "ba", "波音"],
    "CAT": ["caterpillar", "cat", "開拓重工"],
    # 台股
    "2330.TW": ["台積電", "tsmc", "2330"],
    "2454.TW": ["聯發科", "mtk", "2454"],
    "2317.TW": ["鴻海", "foxconn", "2317"],
    "2308.TW": ["台達電", "delta", "2308"],
    "2382.TW": ["廣達", "quanta", "2382"],
    "2303.TW": ["聯電", "umc", "2303"],
    "3711.TW": ["日月光", "ase", "3711"],
    "2379.TW": ["瑞昱", "realtek", "2379"],
    "3034.TW": ["聯詠", "novatek", "3034"],
    "2357.TW": ["華碩", "asus", "2357"],
    "3231.TW": ["緯創", "wistron", "3231"],
    "6669.TW": ["緯穎", "wiwynn", "6669"],
    "2408.TW": ["南亞科", "nanya", "2408"],
    "2412.TW": ["中華電", "cht", "2412"],
    "1301.TW": ["台塑", "formosa", "1301"],
    "2002.TW": ["中鋼", "csc", "2002"],
    "1101.TW": ["台泥", "tcc", "1101"],
}

# 熱門話題關鍵字
TRENDING_KEYWORDS = {
    # AI 相關
    "AI": ["ai", "人工智慧", "artificial intelligence", "機器學習", "deep learning"],
    "ChatGPT": ["chatgpt", "gpt", "openai", "claude", "gemini"],
    "AI晶片": ["ai chip", "gpu", "h100", "h200", "b100", "blackwell"],
    # 財經主題
    "財報": ["earnings", "財報", "quarterly", "季報", "eps", "營收", "revenue"],
    "降息": ["rate cut", "降息", "fed cut", "利率下調"],
    "升息": ["rate hike", "升息", "fed hike", "利率上調"],
    "通膨": ["inflation", "通膨", "cpi", "物價"],
    "衰退": ["recession", "衰退", "經濟衰退"],
    "裁員": ["layoff", "裁員", "job cut", "workforce reduction"],
    # 市場主題
    "IPO": ["ipo", "上市", "首次公開發行"],
    "併購": ["merger", "acquisition", "m&a", "併購", "收購"],
    "回購": ["buyback", "回購", "股票回購"],
    "分拆": ["spin-off", "spinoff", "分拆"],
    # 地緣政治
    "關稅": ["tariff", "關稅", "trade war", "貿易戰"],
    "制裁": ["sanction", "制裁", "ban", "禁令"],
    "中國": ["china", "中國", "beijing", "北京"],
    "台灣": ["taiwan", "台灣", "taipei"],
    # 加密貨幣
    "比特幣": ["bitcoin", "btc", "比特幣"],
    "以太坊": ["ethereum", "eth", "以太坊"],
}


class DailyHotStocksAnalyzer:
    """每日熱門股票分析器"""

    def __init__(self, news_db: str = "news.db", finance_db: str = "finance.db"):
        self.news_db = news_db
        self.finance_db = finance_db

    def get_news_conn(self):
        return sqlite3.connect(self.news_db)

    def get_finance_conn(self):
        return sqlite3.connect(self.finance_db)

    def get_daily_news(self, target_date: date) -> List[Dict]:
        """取得指定日期的新聞"""
        conn = self.get_news_conn()
        cursor = conn.cursor()

        query = """
            SELECT id, title, content, source, source_type,
                   COALESCE(
                       CASE WHEN source_type = 'ptt' THEN published_at ELSE collected_at END,
                       collected_at
                   ) as news_date
            FROM news
            WHERE DATE(COALESCE(
                CASE WHEN source_type = 'ptt' THEN published_at ELSE collected_at END,
                collected_at
            )) = ?
        """

        cursor.execute(query, (target_date.strftime('%Y-%m-%d'),))
        rows = cursor.fetchall()
        conn.close()

        return [
            {
                "id": row[0],
                "title": row[1],
                "content": row[2],
                "source": row[3],
                "source_type": row[4],
                "date": row[5]
            }
            for row in rows
        ]

    def analyze_stock_mentions(self, news_list: List[Dict]) -> List[Dict]:
        """
        分析新聞中提到的股票及其情緒

        Returns:
            List of {symbol, name, mentions, bullish, bearish, sentiment, sample_news}
        """
        stock_data = {}

        for news in news_list:
            text = (news.get("title", "") + " " + (news.get("content") or "")).lower()

            # 檢查每個股票是否被提及
            for symbol, keywords in STOCK_KEYWORDS.items():
                mentioned = any(kw.lower() in text for kw in keywords)

                if mentioned:
                    if symbol not in stock_data:
                        stock_data[symbol] = {
                            "symbol": symbol,
                            "mentions": 0,
                            "bullish": 0,
                            "bearish": 0,
                            "news_ids": [],
                            "sample_titles": []
                        }

                    stock_data[symbol]["mentions"] += 1
                    stock_data[symbol]["news_ids"].append(news["id"])

                    if len(stock_data[symbol]["sample_titles"]) < 3:
                        stock_data[symbol]["sample_titles"].append(news["title"])

                    # 計算該新聞的情緒
                    pos = sum(1 for kw in POSITIVE_KEYWORDS if kw.lower() in text)
                    neg = sum(1 for kw in NEGATIVE_KEYWORDS if kw.lower() in text)

                    if pos > neg:
                        stock_data[symbol]["bullish"] += 1
                    elif neg > pos:
                        stock_data[symbol]["bearish"] += 1

        # 計算情緒分數並排序
        results = []
        for symbol, data in stock_data.items():
            total = data["bullish"] + data["bearish"]
            if total > 0:
                sentiment_score = (data["bullish"] - data["bearish"]) / total
            else:
                sentiment_score = 0

            # 判斷情緒
            if sentiment_score > 0.2:
                sentiment = "🟢 看多"
            elif sentiment_score < -0.2:
                sentiment = "🔴 看空"
            else:
                sentiment = "🟡 中性"

            results.append({
                "symbol": symbol,
                "mentions": data["mentions"],
                "bullish": data["bullish"],
                "bearish": data["bearish"],
                "sentiment_score": sentiment_score,
                "sentiment": sentiment,
                "sample_titles": data["sample_titles"]
            })

        # 按提及次數排序
        results.sort(key=lambda x: x["mentions"], reverse=True)
        return results

    def analyze_trending_keywords(self, news_list: List[Dict]) -> List[Dict]:
        """
        分析熱門關鍵字

        Returns:
            List of {keyword, mentions, sentiment}
        """
        keyword_data = {}

        for news in news_list:
            text = (news.get("title", "") + " " + (news.get("content") or "")).lower()

            for topic, keywords in TRENDING_KEYWORDS.items():
                mentioned = any(kw.lower() in text for kw in keywords)

                if mentioned:
                    if topic not in keyword_data:
                        keyword_data[topic] = {
                            "keyword": topic,
                            "mentions": 0,
                            "bullish": 0,
                            "bearish": 0
                        }

                    keyword_data[topic]["mentions"] += 1

                    # 計算情緒
                    pos = sum(1 for kw in POSITIVE_KEYWORDS if kw.lower() in text)
                    neg = sum(1 for kw in NEGATIVE_KEYWORDS if kw.lower() in text)

                    if pos > neg:
                        keyword_data[topic]["bullish"] += 1
                    elif neg > pos:
                        keyword_data[topic]["bearish"] += 1

        # 計算情緒分數
        results = []
        for topic, data in keyword_data.items():
            total = data["bullish"] + data["bearish"]
            if total > 0:
                sentiment_score = (data["bullish"] - data["bearish"]) / total
            else:
                sentiment_score = 0

            if sentiment_score > 0.2:
                sentiment = "🟢 正面"
            elif sentiment_score < -0.2:
                sentiment = "🔴 負面"
            else:
                sentiment = "🟡 中性"

            results.append({
                "keyword": topic,
                "mentions": data["mentions"],
                "bullish": data["bullish"],
                "bearish": data["bearish"],
                "sentiment_score": sentiment_score,
                "sentiment": sentiment
            })

        results.sort(key=lambda x: x["mentions"], reverse=True)
        return results

    def get_daily_summary(self, target_date: date) -> Dict:
        """
        取得每日情緒摘要

        Returns:
            {
                date, news_count,
                hot_stocks: [...],
                trending_keywords: [...],
                overall_sentiment
            }
        """
        news_list = self.get_daily_news(target_date)

        if not news_list:
            return {
                "date": target_date,
                "news_count": 0,
                "hot_stocks": [],
                "trending_keywords": [],
                "overall_sentiment": "無數據"
            }

        hot_stocks = self.analyze_stock_mentions(news_list)
        trending_keywords = self.analyze_trending_keywords(news_list)

        # 計算整體情緒
        all_text = " ".join([
            (n.get("title", "") + " " + (n.get("content") or "")).lower()
            for n in news_list
        ])

        pos = sum(1 for kw in POSITIVE_KEYWORDS if kw.lower() in all_text)
        neg = sum(1 for kw in NEGATIVE_KEYWORDS if kw.lower() in all_text)

        if pos > neg * 1.3:
            overall = "🟢 整體偏多"
        elif neg > pos * 1.3:
            overall = "🔴 整體偏空"
        else:
            overall = "🟡 多空交織"

        return {
            "date": target_date,
            "news_count": len(news_list),
            "hot_stocks": hot_stocks[:20],  # Top 20
            "trending_keywords": trending_keywords[:15],  # Top 15
            "overall_sentiment": overall,
            "positive_count": pos,
            "negative_count": neg
        }

    def get_weekly_hot_stocks(self, end_date: date, days: int = 7) -> List[Dict]:
        """取得一週內的熱門股票統計"""
        all_stocks = {}

        for i in range(days):
            target_date = end_date - timedelta(days=i)
            daily = self.get_daily_summary(target_date)

            for stock in daily.get("hot_stocks", []):
                symbol = stock["symbol"]
                if symbol not in all_stocks:
                    all_stocks[symbol] = {
                        "symbol": symbol,
                        "total_mentions": 0,
                        "total_bullish": 0,
                        "total_bearish": 0,
                        "days_mentioned": 0
                    }

                all_stocks[symbol]["total_mentions"] += stock["mentions"]
                all_stocks[symbol]["total_bullish"] += stock["bullish"]
                all_stocks[symbol]["total_bearish"] += stock["bearish"]
                all_stocks[symbol]["days_mentioned"] += 1

        # 計算情緒並排序
        results = []
        for symbol, data in all_stocks.items():
            total = data["total_bullish"] + data["total_bearish"]
            if total > 0:
                score = (data["total_bullish"] - data["total_bearish"]) / total
            else:
                score = 0

            if score > 0.2:
                sentiment = "🟢 看多"
            elif score < -0.2:
                sentiment = "🔴 看空"
            else:
                sentiment = "🟡 中性"

            results.append({
                "symbol": symbol,
                "total_mentions": data["total_mentions"],
                "days_mentioned": data["days_mentioned"],
                "bullish": data["total_bullish"],
                "bearish": data["total_bearish"],
                "sentiment_score": score,
                "sentiment": sentiment
            })

        results.sort(key=lambda x: x["total_mentions"], reverse=True)
        return results[:30]


class SentimentBacktester:
    """新聞情緒回測分析器"""

    def __init__(self, news_db: str = "news.db", finance_db: str = "finance.db"):
        self.news_db = news_db
        self.finance_db = finance_db

    def get_news_conn(self):
        return sqlite3.connect(self.news_db)

    def get_finance_conn(self):
        return sqlite3.connect(self.finance_db)

    def calculate_daily_sentiment(self, start_date: date, end_date: date) -> pd.DataFrame:
        """
        計算每日整體新聞情緒分數

        Returns:
            DataFrame with columns: date, positive_count, negative_count,
                                   sentiment_score, news_count
        """
        conn = self.get_news_conn()

        # 取得日期範圍內的新聞
        query = """
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
            ORDER BY news_date
        """

        df = pd.read_sql_query(query, conn, params=(
            start_date.strftime('%Y-%m-%d'),
            end_date.strftime('%Y-%m-%d')
        ))
        conn.close()

        if df.empty:
            return pd.DataFrame()

        # 計算每日情緒
        daily_sentiment = []
        for news_date, group in df.groupby('news_date'):
            text_all = " ".join([
                (str(row['title']) + " " + str(row['content'] or "")).lower()
                for _, row in group.iterrows()
            ])

            pos_count = sum(1 for kw in POSITIVE_KEYWORDS if kw in text_all)
            neg_count = sum(1 for kw in NEGATIVE_KEYWORDS if kw in text_all)

            # 計算情緒分數 (-1 到 1)
            total = pos_count + neg_count
            if total > 0:
                score = (pos_count - neg_count) / total
            else:
                score = 0

            daily_sentiment.append({
                'date': news_date,
                'positive_count': pos_count,
                'negative_count': neg_count,
                'sentiment_score': score,
                'news_count': len(group)
            })

        result = pd.DataFrame(daily_sentiment)
        result['date'] = pd.to_datetime(result['date'])
        return result

    def calculate_category_sentiment(self, category: str, keywords: List[str],
                                     start_date: date, end_date: date) -> pd.DataFrame:
        """
        計算特定產業類別的每日情緒分數

        Args:
            category: 產業類別名稱
            keywords: 該類別的關鍵字列表
            start_date: 開始日期
            end_date: 結束日期

        Returns:
            DataFrame with daily sentiment for this category
        """
        conn = self.get_news_conn()

        # 建立關鍵字搜尋條件
        keyword_conditions = " OR ".join([
            f"LOWER(title || ' ' || COALESCE(content, '')) LIKE '%{kw.lower()}%'"
            for kw in keywords
        ])

        query = f"""
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
            ORDER BY news_date
        """

        df = pd.read_sql_query(query, conn, params=(
            start_date.strftime('%Y-%m-%d'),
            end_date.strftime('%Y-%m-%d')
        ))
        conn.close()

        if df.empty:
            return pd.DataFrame()

        # 計算每日情緒
        daily_sentiment = []
        for news_date, group in df.groupby('news_date'):
            text_all = " ".join([
                (str(row['title']) + " " + str(row['content'] or "")).lower()
                for _, row in group.iterrows()
            ])

            pos_count = sum(1 for kw in POSITIVE_KEYWORDS if kw in text_all)
            neg_count = sum(1 for kw in NEGATIVE_KEYWORDS if kw in text_all)

            total = pos_count + neg_count
            if total > 0:
                score = (pos_count - neg_count) / total
            else:
                score = 0

            daily_sentiment.append({
                'date': news_date,
                'category': category,
                'positive_count': pos_count,
                'negative_count': neg_count,
                'sentiment_score': score,
                'news_count': len(group)
            })

        result = pd.DataFrame(daily_sentiment)
        if not result.empty:
            result['date'] = pd.to_datetime(result['date'])
        return result

    def get_etf_returns(self, symbol: str, start_date: date, end_date: date) -> pd.DataFrame:
        """
        取得 ETF 的每日報酬率

        Returns:
            DataFrame with columns: date, close, return_1d, return_5d
        """
        conn = self.get_finance_conn()

        query = """
            SELECT date, close
            FROM daily_prices
            WHERE symbol = ?
            AND date BETWEEN ? AND ?
            ORDER BY date
        """

        df = pd.read_sql_query(query, conn, params=(
            symbol,
            start_date.strftime('%Y-%m-%d'),
            end_date.strftime('%Y-%m-%d')
        ))
        conn.close()

        if df.empty:
            return pd.DataFrame()

        df['date'] = pd.to_datetime(df['date'])

        # 計算報酬率
        df['return_1d'] = df['close'].pct_change(1) * 100  # 1日報酬率 (%)
        df['return_5d'] = df['close'].pct_change(5) * 100  # 5日報酬率 (%)
        df['return_next_1d'] = df['return_1d'].shift(-1)   # 隔日報酬率
        df['return_next_5d'] = df['close'].pct_change(5).shift(-5) * 100  # 未來5日報酬率

        return df

    def run_backtest(self, etf_symbol: str = "SPY",
                     start_date: date = None,
                     end_date: date = None,
                     lead_days: int = 1) -> Dict:
        """
        執行回測：比較新聞情緒與 ETF 表現

        Args:
            etf_symbol: ETF 代碼
            start_date: 開始日期
            end_date: 結束日期
            lead_days: 領先天數（情緒領先價格多少天）

        Returns:
            回測結果字典
        """
        if end_date is None:
            end_date = date.today()
        if start_date is None:
            start_date = end_date - timedelta(days=365)

        # 取得情緒數據
        sentiment_df = self.calculate_daily_sentiment(start_date, end_date)
        if sentiment_df.empty:
            return {"error": "無新聞數據"}

        # 取得 ETF 數據
        etf_df = self.get_etf_returns(etf_symbol, start_date, end_date)
        if etf_df.empty:
            return {"error": f"無 {etf_symbol} 價格數據"}

        # 合併數據
        merged = pd.merge(sentiment_df, etf_df, on='date', how='inner')

        if merged.empty:
            return {"error": "無法合併數據"}

        # 計算領先指標（情緒領先價格 N 天）
        merged['sentiment_lagged'] = merged['sentiment_score'].shift(lead_days)

        # 移除 NaN
        analysis_df = merged.dropna(subset=['sentiment_lagged', 'return_1d'])

        if len(analysis_df) < 10:
            return {"error": "數據點不足"}

        # 計算相關性
        correlation = analysis_df['sentiment_lagged'].corr(analysis_df['return_1d'])

        # 計算勝率
        # 當情緒為正（>0），隔日上漲的機率
        positive_sentiment = analysis_df[analysis_df['sentiment_lagged'] > 0]
        if len(positive_sentiment) > 0:
            win_rate_positive = (positive_sentiment['return_1d'] > 0).mean() * 100
        else:
            win_rate_positive = 0

        # 當情緒為負（<0），隔日下跌的機率
        negative_sentiment = analysis_df[analysis_df['sentiment_lagged'] < 0]
        if len(negative_sentiment) > 0:
            win_rate_negative = (negative_sentiment['return_1d'] < 0).mean() * 100
        else:
            win_rate_negative = 0

        # 計算情緒極端值的預測力
        # 情緒極度樂觀（>0.3）
        very_positive = analysis_df[analysis_df['sentiment_lagged'] > 0.3]
        if len(very_positive) > 0:
            avg_return_very_positive = very_positive['return_1d'].mean()
            win_rate_very_positive = (very_positive['return_1d'] > 0).mean() * 100
        else:
            avg_return_very_positive = 0
            win_rate_very_positive = 0

        # 情緒極度悲觀（<-0.3）
        very_negative = analysis_df[analysis_df['sentiment_lagged'] < -0.3]
        if len(very_negative) > 0:
            avg_return_very_negative = very_negative['return_1d'].mean()
            win_rate_very_negative = (very_negative['return_1d'] > 0).mean() * 100
        else:
            avg_return_very_negative = 0
            win_rate_very_negative = 0

        # 計算不同領先天數的相關性
        lead_correlations = {}
        for lead in [1, 2, 3, 5, 7]:
            temp_df = merged.copy()
            temp_df['sent_lead'] = temp_df['sentiment_score'].shift(lead)
            temp_df = temp_df.dropna(subset=['sent_lead', 'return_1d'])
            if len(temp_df) > 10:
                lead_correlations[lead] = temp_df['sent_lead'].corr(temp_df['return_1d'])

        return {
            "etf_symbol": etf_symbol,
            "period": f"{start_date} ~ {end_date}",
            "data_points": len(analysis_df),
            "lead_days": lead_days,
            "correlation": correlation,
            "lead_correlations": lead_correlations,
            "win_rate": {
                "positive_sentiment_up": win_rate_positive,
                "negative_sentiment_down": win_rate_negative,
                "overall": (win_rate_positive + win_rate_negative) / 2
            },
            "extreme_sentiment": {
                "very_positive": {
                    "count": len(very_positive),
                    "avg_return": avg_return_very_positive,
                    "win_rate": win_rate_very_positive
                },
                "very_negative": {
                    "count": len(very_negative),
                    "avg_return": avg_return_very_negative,
                    "win_rate": win_rate_very_negative  # 這裡是上漲機率
                }
            },
            "sentiment_stats": {
                "mean": analysis_df['sentiment_score'].mean(),
                "std": analysis_df['sentiment_score'].std(),
                "min": analysis_df['sentiment_score'].min(),
                "max": analysis_df['sentiment_score'].max()
            },
            "return_stats": {
                "mean": analysis_df['return_1d'].mean(),
                "std": analysis_df['return_1d'].std(),
                "total_return": ((1 + analysis_df['return_1d']/100).prod() - 1) * 100
            },
            "merged_data": merged  # 用於繪圖
        }

    def run_multi_etf_backtest(self, etf_symbols: List[str] = None,
                                start_date: date = None,
                                end_date: date = None) -> List[Dict]:
        """
        對多個 ETF 執行回測
        """
        if etf_symbols is None:
            etf_symbols = PRIMARY_ETFS

        results = []
        for symbol in etf_symbols:
            try:
                result = self.run_backtest(symbol, start_date, end_date)
                if "error" not in result:
                    results.append(result)
            except Exception as e:
                logger.error(f"回測 {symbol} 失敗: {e}")

        return results

    def get_sentiment_signal(self, lookback_days: int = 7) -> Dict:
        """
        取得當前情緒信號

        Args:
            lookback_days: 回顧天數

        Returns:
            當前情緒信號和建議
        """
        end_date = date.today()
        start_date = end_date - timedelta(days=lookback_days)

        sentiment_df = self.calculate_daily_sentiment(start_date, end_date)

        if sentiment_df.empty:
            return {"signal": "NEUTRAL", "score": 0, "message": "無足夠數據"}

        # 計算近期平均情緒
        avg_sentiment = sentiment_df['sentiment_score'].mean()
        recent_sentiment = sentiment_df['sentiment_score'].iloc[-1] if len(sentiment_df) > 0 else 0

        # 計算情緒趨勢
        if len(sentiment_df) >= 3:
            trend = sentiment_df['sentiment_score'].iloc[-3:].mean() - \
                    sentiment_df['sentiment_score'].iloc[:3].mean()
        else:
            trend = 0

        # 判斷信號
        if avg_sentiment > 0.2 and trend > 0:
            signal = "BULLISH"
            message = "情緒樂觀且持續向上，市場可能延續漲勢"
        elif avg_sentiment < -0.2 and trend < 0:
            signal = "BEARISH"
            message = "情緒悲觀且持續向下，市場可能延續跌勢"
        elif avg_sentiment > 0.3:
            signal = "CAUTION_HIGH"
            message = "情緒過度樂觀，注意可能的反轉風險"
        elif avg_sentiment < -0.3:
            signal = "CONTRARIAN_BUY"
            message = "情緒極度悲觀，可能是逆勢佈局機會"
        else:
            signal = "NEUTRAL"
            message = "情緒中性，市場方向不明確"

        return {
            "signal": signal,
            "avg_sentiment": avg_sentiment,
            "recent_sentiment": recent_sentiment,
            "trend": trend,
            "message": message,
            "data": sentiment_df
        }


def run_full_analysis():
    """執行完整分析並輸出報告"""
    backtester = SentimentBacktester()

    print("=" * 60)
    print("新聞情緒 vs ETF 回測分析")
    print("=" * 60)

    # 對主要 ETF 執行回測
    results = backtester.run_multi_etf_backtest()

    for result in results:
        print(f"\n📊 {result['etf_symbol']}")
        print(f"   期間: {result['period']}")
        print(f"   數據點: {result['data_points']}")
        print(f"   相關性: {result['correlation']:.4f}")
        print(f"   勝率 (情緒正→漲): {result['win_rate']['positive_sentiment_up']:.1f}%")
        print(f"   勝率 (情緒負→跌): {result['win_rate']['negative_sentiment_down']:.1f}%")

    # 當前信號
    signal = backtester.get_sentiment_signal()
    print(f"\n{'=' * 60}")
    print(f"📡 當前情緒信號: {signal['signal']}")
    print(f"   {signal['message']}")

    return results


if __name__ == "__main__":
    run_full_analysis()
