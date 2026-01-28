"""
總經指標配置 - FRED API 指標定義與分析參數
"""

# FRED API 設定
FRED_API_KEY = "38a82d7d14a06ca27054a3a8c7946877"

# 總經指標定義
MACRO_INDICATORS = [
    # 殖利率曲線 (Yield Curve) - 最重要的領先指標
    {
        "series_id": "T10Y2Y",
        "name": "10年-2年公債利差",
        "name_en": "10-Year Treasury Minus 2-Year Treasury",
        "frequency": "daily",
        "category": "yield_curve",
        "description": "殖利率曲線倒掛是衰退的領先指標",
        "unit": "percent"
    },
    {
        "series_id": "T10Y3M",
        "name": "10年-3月公債利差",
        "name_en": "10-Year Treasury Minus 3-Month Treasury",
        "frequency": "daily",
        "category": "yield_curve",
        "description": "補充判斷殖利率曲線狀態",
        "unit": "percent"
    },
    # 就業市場 (Employment)
    {
        "series_id": "UNRATE",
        "name": "失業率",
        "name_en": "Unemployment Rate",
        "frequency": "monthly",
        "category": "employment",
        "description": "勞動市場健康度指標",
        "unit": "percent"
    },
    {
        "series_id": "PAYEMS",
        "name": "非農就業人數",
        "name_en": "All Employees: Total Nonfarm",
        "frequency": "monthly",
        "category": "employment",
        "description": "經濟活動的重要指標",
        "unit": "thousands"
    },
    {
        "series_id": "ICSA",
        "name": "初領失業救濟金人數",
        "name_en": "Initial Claims",
        "frequency": "weekly",
        "category": "employment",
        "description": "就業市場的領先指標",
        "unit": "number"
    },
    # 經濟成長 (Growth)
    {
        "series_id": "GDP",
        "name": "國內生產總值",
        "name_en": "Gross Domestic Product",
        "frequency": "quarterly",
        "category": "growth",
        "description": "經濟總產出",
        "unit": "billions"
    },
    {
        "series_id": "INDPRO",
        "name": "工業生產指數",
        "name_en": "Industrial Production Index",
        "frequency": "monthly",
        "category": "growth",
        "description": "製造業活動指標",
        "unit": "index"
    },
    # 通貨膨脹 (Inflation)
    {
        "series_id": "CPIAUCSL",
        "name": "消費者物價指數",
        "name_en": "Consumer Price Index",
        "frequency": "monthly",
        "category": "inflation",
        "description": "物價水準指標",
        "unit": "index"
    },
    # 利率政策 (Interest Rate)
    {
        "series_id": "FEDFUNDS",
        "name": "聯邦基金利率",
        "name_en": "Federal Funds Effective Rate",
        "frequency": "monthly",
        "category": "interest_rate",
        "description": "聯準會貨幣政策方向",
        "unit": "percent"
    },
    # 市場情緒 (Sentiment)
    {
        "series_id": "UMCSENT",
        "name": "密西根消費者信心指數",
        "name_en": "University of Michigan Consumer Sentiment",
        "frequency": "monthly",
        "category": "sentiment",
        "description": "消費意願指標",
        "unit": "index"
    },
    {
        "series_id": "VIXCLS",
        "name": "VIX 恐慌指數",
        "name_en": "CBOE Volatility Index",
        "frequency": "daily",
        "category": "sentiment",
        "description": "市場恐慌程度指標",
        "unit": "index"
    },
]

# 分析維度權重
DIMENSION_WEIGHTS = {
    "yield_curve": 0.25,    # 殖利率曲線 - 最可靠的領先指標
    "growth": 0.25,         # GDP/成長 - 經濟基本面
    "employment": 0.20,     # 就業市場 - 經濟健康度
    "inflation": 0.15,      # 通膨 - Fed政策方向
    "sentiment": 0.15,      # 市場情緒 - 投資人心理
}

# 市場週期定義
MARKET_CYCLES = {
    "EXPANSION": {
        "name": "擴張期",
        "name_en": "Expansion",
        "color": "#00C851",  # 綠色
        "emoji": "📈",
        "description": "經濟成長、就業增加、企業獲利上升"
    },
    "PEAK": {
        "name": "高峰期",
        "name_en": "Peak",
        "color": "#ffbb33",  # 黃色
        "emoji": "⚠️",
        "description": "經濟過熱、通膨上升、Fed可能升息"
    },
    "CONTRACTION": {
        "name": "收縮期",
        "name_en": "Contraction",
        "color": "#ff4444",  # 紅色
        "emoji": "📉",
        "description": "經濟放緩、企業獲利下滑、失業上升"
    },
    "TROUGH": {
        "name": "谷底期",
        "name_en": "Trough",
        "color": "#33b5e5",  # 藍色
        "emoji": "🔄",
        "description": "經濟觸底、Fed寬鬆、復甦跡象出現"
    },
}

# 週期策略配置
CYCLE_STRATEGIES = {
    "EXPANSION": {
        "name": "積極成長策略",
        "stock_allocation": 0.80,  # 80% 股票
        "bond_allocation": 0.15,
        "cash_allocation": 0.05,
        "preferred_sectors": ["Technology", "Consumer Discretionary", "Financials", "Industrials"],
        "avoid_sectors": ["Utilities", "Consumer Staples"],
        "investment_style": ["Growth", "Momentum"],
        "risk_tolerance": "high"
    },
    "PEAK": {
        "name": "謹慎觀望策略",
        "stock_allocation": 0.55,  # 55% 股票
        "bond_allocation": 0.30,
        "cash_allocation": 0.15,
        "preferred_sectors": ["Healthcare", "Consumer Staples", "Utilities"],
        "avoid_sectors": ["Technology", "Consumer Discretionary"],
        "investment_style": ["Value", "Quality"],
        "risk_tolerance": "medium"
    },
    "CONTRACTION": {
        "name": "防禦配置策略",
        "stock_allocation": 0.30,  # 30% 股票
        "bond_allocation": 0.45,
        "cash_allocation": 0.25,
        "preferred_sectors": ["Utilities", "Consumer Staples", "Healthcare"],
        "avoid_sectors": ["Technology", "Financials", "Consumer Discretionary"],
        "investment_style": ["Defensive", "Low Volatility"],
        "risk_tolerance": "low"
    },
    "TROUGH": {
        "name": "逐步加碼策略",
        "stock_allocation": 0.60,  # 60% 股票
        "bond_allocation": 0.25,
        "cash_allocation": 0.15,
        "preferred_sectors": ["Materials", "Industrials", "Financials", "Energy"],
        "avoid_sectors": ["Utilities"],
        "investment_style": ["Cyclical", "Value"],
        "risk_tolerance": "medium-high"
    },
}

# 指標閾值設定
INDICATOR_THRESHOLDS = {
    # 殖利率曲線
    "yield_curve_inversion": 0,           # 低於此值表示倒掛
    "yield_curve_steep": 1.5,             # 高於此值表示陡峭

    # 失業率
    "unemployment_low": 4.0,              # 低失業率
    "unemployment_high": 6.0,             # 高失業率
    "unemployment_crisis": 8.0,           # 危機水準

    # VIX
    "vix_low": 15,                        # 低波動
    "vix_elevated": 20,                   # 波動升高
    "vix_high": 30,                       # 高波動/恐慌

    # 消費者信心
    "sentiment_high": 100,                # 高信心
    "sentiment_low": 70,                  # 低信心

    # GDP 成長率 (季度環比年化)
    "gdp_strong": 3.0,                    # 強勁成長
    "gdp_weak": 1.0,                      # 弱成長
    "gdp_recession": 0,                   # 衰退
}

# 資料庫設定
MACRO_DATABASE_PATH = "macro.db"

# 排程設定
MACRO_SCHEDULE = {
    "daily_update_time": "08:00",         # 每日更新時間
    "weekly_full_update_day": "sunday",   # 每週完整更新日
    "weekly_full_update_time": "06:00",   # 完整更新時間
}
