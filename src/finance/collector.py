"""
yfinance 數據收集器
"""

import logging
from datetime import datetime, date, timedelta
from typing import List, Optional, Dict
import time

import yfinance as yf

from .database import FinanceDatabase

logger = logging.getLogger(__name__)


class YFinanceCollector:
    """yfinance 數據收集器"""

    def __init__(self, db: FinanceDatabase):
        """
        初始化收集器

        Args:
            db: FinanceDatabase 實例
        """
        self.db = db

    def add_symbols(self, symbols: List[str], market: str = "US",
                    fetch_info: bool = True, max_retries: int = 3) -> int:
        """
        新增股票代碼到追蹤清單

        Args:
            symbols: 股票代碼列表
            market: 市場 (US/TW/ETF/INDEX)
            fetch_info: 是否從 yfinance 取得股票資訊（可跳過以避免 rate limit）
            max_retries: 最大重試次數

        Returns:
            新增數量
        """
        added = 0
        total = len(symbols)

        for i, symbol in enumerate(symbols, 1):
            logger.info(f"[{i}/{total}] 處理 {symbol}...")

            name = symbol
            sector = None
            industry = None

            if fetch_info:
                # 使用重試機制取得股票資訊
                for retry in range(max_retries):
                    try:
                        ticker = yf.Ticker(symbol)
                        info = ticker.info

                        name = info.get("longName") or info.get("shortName") or symbol
                        sector = info.get("sector")
                        industry = info.get("industry")
                        break  # 成功則跳出重試迴圈

                    except Exception as e:
                        if "429" in str(e) or "Too Many Requests" in str(e):
                            wait_time = (retry + 1) * 5  # 指數退避：5, 10, 15 秒
                            logger.warning(f"  Rate limit hit, 等待 {wait_time} 秒後重試 ({retry+1}/{max_retries})...")
                            time.sleep(wait_time)
                        else:
                            logger.warning(f"  取得資訊失敗: {e}")
                            break

            try:
                if self.db.add_to_watchlist(
                    symbol=symbol,
                    name=name,
                    market=market,
                    sector=sector,
                    industry=industry
                ):
                    added += 1
                    logger.info(f"  [+] 新增 {symbol}: {name}")
                else:
                    logger.info(f"  [=] 更新 {symbol}: {name}")

            except Exception as e:
                logger.error(f"  [!] {symbol} 新增失敗: {e}")

            # 增加延遲避免 rate limit（2秒基礎延遲）
            if fetch_info:
                time.sleep(2.0)

        return added

    def collect_daily_prices(self, symbols: List[str] = None, period: str = "5d") -> Dict[str, int]:
        """
        收集每日價格數據

        Args:
            symbols: 股票代碼列表（None 則使用 watchlist）
            period: 資料期間 (1d, 5d, 1mo, 3mo, 6mo, 1y, 2y, 5y, 10y, ytd, max)

        Returns:
            收集結果統計
        """
        if symbols is None:
            symbols = self.db.get_symbols()

        if not symbols:
            logger.warning("沒有股票需要收集")
            return {"collected": 0, "inserted": 0, "errors": 0}

        stats = {"collected": 0, "inserted": 0, "errors": 0}

        for symbol in symbols:
            try:
                logger.info(f"收集 {symbol} 價格數據...")

                ticker = yf.Ticker(symbol)
                hist = ticker.history(period=period)

                if hist.empty:
                    logger.warning(f"  {symbol}: 無數據")
                    continue

                data = []
                for idx, row in hist.iterrows():
                    data.append({
                        "symbol": symbol,
                        "date": idx.date(),
                        "open": row["Open"],
                        "high": row["High"],
                        "low": row["Low"],
                        "close": row["Close"],
                        "adj_close": row["Close"],  # yfinance 已調整
                        "volume": int(row["Volume"]) if row["Volume"] else 0
                    })

                inserted = self.db.insert_daily_prices_bulk(data)
                stats["collected"] += len(data)
                stats["inserted"] += inserted

                logger.info(f"  {symbol}: 收集 {len(data)} 筆, 新增 {inserted} 筆")

            except Exception as e:
                logger.error(f"  {symbol} 錯誤: {e}")
                stats["errors"] += 1

            time.sleep(0.3)

        return stats

    def collect_historical_data(self, symbols: List[str] = None,
                                start_date: str = None, end_date: str = None,
                                period: str = "1y") -> Dict[str, int]:
        """
        收集歷史數據

        Args:
            symbols: 股票代碼列表
            start_date: 開始日期 (YYYY-MM-DD)
            end_date: 結束日期 (YYYY-MM-DD)
            period: 期間（當 start_date 為 None 時使用）

        Returns:
            收集結果統計
        """
        if symbols is None:
            symbols = self.db.get_symbols()

        if not symbols:
            logger.warning("沒有股票需要收集")
            return {"collected": 0, "inserted": 0, "errors": 0}

        stats = {"collected": 0, "inserted": 0, "errors": 0}

        for symbol in symbols:
            try:
                logger.info(f"收集 {symbol} 歷史數據...")

                ticker = yf.Ticker(symbol)

                if start_date:
                    hist = ticker.history(start=start_date, end=end_date)
                else:
                    hist = ticker.history(period=period)

                if hist.empty:
                    logger.warning(f"  {symbol}: 無數據")
                    continue

                data = []
                for idx, row in hist.iterrows():
                    data.append({
                        "symbol": symbol,
                        "date": idx.date(),
                        "open": row["Open"],
                        "high": row["High"],
                        "low": row["Low"],
                        "close": row["Close"],
                        "adj_close": row["Close"],
                        "volume": int(row["Volume"]) if row["Volume"] else 0
                    })

                inserted = self.db.insert_daily_prices_bulk(data)
                stats["collected"] += len(data)
                stats["inserted"] += inserted

                logger.info(f"  {symbol}: 收集 {len(data)} 筆, 新增 {inserted} 筆")

            except Exception as e:
                logger.error(f"  {symbol} 錯誤: {e}")
                stats["errors"] += 1

            time.sleep(0.5)

        return stats

    def collect_fundamentals(self, symbols: List[str] = None) -> Dict[str, int]:
        """
        收集基本面數據

        Args:
            symbols: 股票代碼列表

        Returns:
            收集結果統計
        """
        if symbols is None:
            symbols = self.db.get_symbols()

        if not symbols:
            logger.warning("沒有股票需要收集")
            return {"collected": 0, "inserted": 0, "errors": 0}

        stats = {"collected": 0, "inserted": 0, "errors": 0}
        today = date.today()

        for symbol in symbols:
            try:
                logger.info(f"收集 {symbol} 基本面數據...")

                ticker = yf.Ticker(symbol)
                info = ticker.info

                if not info or info.get("regularMarketPrice") is None:
                    logger.warning(f"  {symbol}: 無數據")
                    continue

                data = {
                    "market_cap": info.get("marketCap"),
                    "enterprise_value": info.get("enterpriseValue"),
                    "pe_ratio": info.get("trailingPE"),
                    "forward_pe": info.get("forwardPE"),
                    "peg_ratio": info.get("pegRatio"),
                    "pb_ratio": info.get("priceToBook"),
                    "ps_ratio": info.get("priceToSalesTrailing12Months"),
                    "dividend_yield": info.get("dividendYield"),
                    "eps": info.get("trailingEps"),
                    "revenue": info.get("totalRevenue"),
                    "profit_margin": info.get("profitMargins"),
                    "operating_margin": info.get("operatingMargins"),
                    "roe": info.get("returnOnEquity"),
                    "roa": info.get("returnOnAssets"),
                    "debt_to_equity": info.get("debtToEquity"),
                    "current_ratio": info.get("currentRatio"),
                    "quick_ratio": info.get("quickRatio"),
                    "beta": info.get("beta"),
                    "fifty_two_week_high": info.get("fiftyTwoWeekHigh"),
                    "fifty_two_week_low": info.get("fiftyTwoWeekLow"),
                    "fifty_day_avg": info.get("fiftyDayAverage"),
                    "two_hundred_day_avg": info.get("twoHundredDayAverage"),
                    "avg_volume": info.get("averageVolume"),
                    "shares_outstanding": info.get("sharesOutstanding"),
                    "float_shares": info.get("floatShares"),
                    "held_by_institutions": info.get("heldPercentInstitutions"),
                    "short_ratio": info.get("shortRatio"),
                    "raw_data": {
                        "currency": info.get("currency"),
                        "exchange": info.get("exchange"),
                        "sector": info.get("sector"),
                        "industry": info.get("industry"),
                        "fullTimeEmployees": info.get("fullTimeEmployees"),
                        "recommendationMean": info.get("recommendationMean"),
                        "targetMeanPrice": info.get("targetMeanPrice"),
                    }
                }

                if self.db.insert_fundamentals(symbol, today, data):
                    stats["inserted"] += 1
                    logger.info(f"  {symbol}: 新增基本面數據")
                else:
                    logger.info(f"  {symbol}: 數據已存在")

                stats["collected"] += 1

            except Exception as e:
                logger.error(f"  {symbol} 錯誤: {e}")
                stats["errors"] += 1

            time.sleep(0.5)

        return stats

    def collect_all(self, symbols: List[str] = None, price_period: str = "5d") -> Dict:
        """
        收集所有類型的數據

        Args:
            symbols: 股票代碼列表
            price_period: 價格數據期間

        Returns:
            收集結果統計
        """
        results = {}

        logger.info("=" * 50)
        logger.info("開始收集金融數據")
        logger.info("=" * 50)

        # 收集價格
        logger.info("\n[Phase 1] 收集每日價格")
        logger.info("-" * 30)
        results["prices"] = self.collect_daily_prices(symbols, period=price_period)

        # 收集基本面
        logger.info("\n[Phase 2] 收集基本面數據")
        logger.info("-" * 30)
        results["fundamentals"] = self.collect_fundamentals(symbols)

        # 總結
        logger.info("\n" + "=" * 50)
        logger.info("收集完成總結")
        logger.info("=" * 50)

        total_collected = results["prices"]["collected"] + results["fundamentals"]["collected"]
        total_inserted = results["prices"]["inserted"] + results["fundamentals"]["inserted"]
        total_errors = results["prices"]["errors"] + results["fundamentals"]["errors"]

        logger.info(f"價格數據: 收集 {results['prices']['collected']}, 新增 {results['prices']['inserted']}")
        logger.info(f"基本面: 收集 {results['fundamentals']['collected']}, 新增 {results['fundamentals']['inserted']}")
        logger.info(f"錯誤數: {total_errors}")

        return results


# ========== 預設股票清單 ==========

# 美股 - 主要科技股 & 大型股
US_STOCKS = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA", "BRK-B",
    "JPM", "V", "UNH", "JNJ", "WMT", "MA", "PG", "HD", "XOM", "CVX",
    "BAC", "ABBV", "KO", "PFE", "COST", "MRK", "TMO", "AVGO", "PEP",
    "ORCL", "CSCO", "ADBE", "CRM", "ACN", "MCD", "NKE", "INTC", "AMD",
]

# 台股 - 主要權值股
TW_STOCKS = [
    "2330.TW",  # 台積電
    "2317.TW",  # 鴻海
    "2454.TW",  # 聯發科
    "2412.TW",  # 中華電
    "2882.TW",  # 國泰金
    "2881.TW",  # 富邦金
    "1301.TW",  # 台塑
    "2308.TW",  # 台達電
    "2303.TW",  # 聯電
    "3711.TW",  # 日月光
    "2891.TW",  # 中信金
    "2886.TW",  # 兆豐金
    "2002.TW",  # 中鋼
    "1303.TW",  # 南亞
    "2885.TW",  # 元大金
]

# ETF
ETFS = [
    "SPY",   # S&P 500
    "QQQ",   # NASDAQ 100
    "IWM",   # Russell 2000
    "DIA",   # Dow Jones
    "VTI",   # Total Stock Market
    "VOO",   # S&P 500 (Vanguard)
    "VGT",   # Tech
    "XLF",   # Financials
    "XLE",   # Energy
    "XLV",   # Healthcare
    "GLD",   # Gold
    "SLV",   # Silver
    "TLT",   # 20+ Year Treasury
    "HYG",   # High Yield Bond
    "EEM",   # Emerging Markets
    "VWO",   # Emerging Markets (Vanguard)
    "0050.TW",  # 元大台灣50
    "0056.TW",  # 元大高股息
]

# 指數
INDICES = [
    "^GSPC",   # S&P 500
    "^DJI",    # Dow Jones
    "^IXIC",   # NASDAQ
    "^RUT",    # Russell 2000
    "^VIX",    # VIX 恐慌指數
    "^TNX",    # 10-Year Treasury Yield
    "^TWII",   # 台灣加權指數
]
