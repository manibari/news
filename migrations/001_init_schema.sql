-- ============================================
-- PostgreSQL Schema for Stock News Analysis System
-- 版本: 001
-- 用途: 地端 PostgreSQL 初始化
-- 執行: psql -U postgres -d stock_analysis -f 001_init_schema.sql
-- ============================================

-- ============================================
-- 1. 新聞資料表
-- ============================================
CREATE TABLE IF NOT EXISTS news (
    id BIGSERIAL PRIMARY KEY,
    title TEXT NOT NULL,
    content TEXT,
    url TEXT UNIQUE,
    source TEXT,
    category TEXT,
    published_at TIMESTAMP WITH TIME ZONE,
    collected_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    source_type TEXT
);

COMMENT ON TABLE news IS '新聞資料表';
COMMENT ON COLUMN news.source_type IS '來源類型: rss, api, ptt, scraper';

-- 新聞索引
CREATE INDEX IF NOT EXISTS idx_news_published_at ON news(published_at DESC);
CREATE INDEX IF NOT EXISTS idx_news_source ON news(source);
CREATE INDEX IF NOT EXISTS idx_news_category ON news(category);
CREATE INDEX IF NOT EXISTS idx_news_collected_at ON news(collected_at DESC);

-- ============================================
-- 2. 股票追蹤清單
-- ============================================
CREATE TABLE IF NOT EXISTS watchlist (
    id BIGSERIAL PRIMARY KEY,
    symbol TEXT UNIQUE NOT NULL,
    name TEXT,
    market TEXT,
    sector TEXT,
    industry TEXT,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

COMMENT ON TABLE watchlist IS '股票追蹤清單';
COMMENT ON COLUMN watchlist.market IS '市場: US, TW, ETF';

-- 追蹤清單索引
CREATE INDEX IF NOT EXISTS idx_watchlist_market ON watchlist(market);
CREATE INDEX IF NOT EXISTS idx_watchlist_symbol ON watchlist(symbol);
CREATE INDEX IF NOT EXISTS idx_watchlist_active ON watchlist(is_active) WHERE is_active = TRUE;

-- ============================================
-- 3. 每日價格 (OHLCV)
-- ============================================
CREATE TABLE IF NOT EXISTS daily_prices (
    id BIGSERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    date DATE NOT NULL,
    open REAL,
    high REAL,
    low REAL,
    close REAL,
    adj_close REAL,
    volume BIGINT,
    collected_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    UNIQUE(symbol, date)
);

COMMENT ON TABLE daily_prices IS '每日價格 OHLCV';

-- 價格索引
CREATE INDEX IF NOT EXISTS idx_daily_prices_symbol ON daily_prices(symbol);
CREATE INDEX IF NOT EXISTS idx_daily_prices_date ON daily_prices(date DESC);
CREATE INDEX IF NOT EXISTS idx_daily_prices_symbol_date ON daily_prices(symbol, date DESC);

-- ============================================
-- 4. 基本面數據
-- ============================================
CREATE TABLE IF NOT EXISTS fundamentals (
    id BIGSERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    date DATE NOT NULL,
    market_cap REAL,
    enterprise_value REAL,
    pe_ratio REAL,
    forward_pe REAL,
    peg_ratio REAL,
    pb_ratio REAL,
    ps_ratio REAL,
    dividend_yield REAL,
    eps REAL,
    revenue REAL,
    profit_margin REAL,
    operating_margin REAL,
    roe REAL,
    roa REAL,
    debt_to_equity REAL,
    current_ratio REAL,
    quick_ratio REAL,
    beta REAL,
    fifty_two_week_high REAL,
    fifty_two_week_low REAL,
    fifty_day_avg REAL,
    two_hundred_day_avg REAL,
    avg_volume REAL,
    shares_outstanding REAL,
    float_shares REAL,
    held_by_institutions REAL,
    short_ratio REAL,
    raw_data JSONB,
    collected_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    UNIQUE(symbol, date)
);

COMMENT ON TABLE fundamentals IS '基本面數據';

-- 基本面索引
CREATE INDEX IF NOT EXISTS idx_fundamentals_symbol ON fundamentals(symbol);
CREATE INDEX IF NOT EXISTS idx_fundamentals_date ON fundamentals(date DESC);

-- ============================================
-- 5. 總經指標定義
-- ============================================
CREATE TABLE IF NOT EXISTS macro_indicators (
    id BIGSERIAL PRIMARY KEY,
    series_id TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    frequency TEXT,
    category TEXT,
    description TEXT,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

COMMENT ON TABLE macro_indicators IS '總經指標定義';
COMMENT ON COLUMN macro_indicators.frequency IS '更新頻率: daily, weekly, monthly, quarterly';
COMMENT ON COLUMN macro_indicators.category IS '分類: yield_curve, employment, growth, inflation, sentiment';

-- ============================================
-- 6. 總經數據
-- ============================================
CREATE TABLE IF NOT EXISTS macro_data (
    id BIGSERIAL PRIMARY KEY,
    series_id TEXT NOT NULL,
    date DATE NOT NULL,
    value REAL NOT NULL,
    change_pct REAL,
    collected_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    UNIQUE(series_id, date)
);

COMMENT ON TABLE macro_data IS '總經數據時間序列';

-- 總經數據索引
CREATE INDEX IF NOT EXISTS idx_macro_data_series ON macro_data(series_id);
CREATE INDEX IF NOT EXISTS idx_macro_data_date ON macro_data(date DESC);
CREATE INDEX IF NOT EXISTS idx_macro_data_series_date ON macro_data(series_id, date DESC);

-- ============================================
-- 7. 市場週期記錄
-- ============================================
CREATE TABLE IF NOT EXISTS market_cycles (
    id BIGSERIAL PRIMARY KEY,
    date DATE UNIQUE NOT NULL,
    phase TEXT NOT NULL,
    score REAL NOT NULL,
    confidence REAL,
    signals JSONB,
    recommended_strategy TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

COMMENT ON TABLE market_cycles IS '市場週期記錄';
COMMENT ON COLUMN market_cycles.phase IS '週期階段: EXPANSION, PEAK, CONTRACTION, TROUGH';

-- 市場週期索引
CREATE INDEX IF NOT EXISTS idx_market_cycles_date ON market_cycles(date DESC);
CREATE INDEX IF NOT EXISTS idx_market_cycles_phase ON market_cycles(phase);

-- ============================================
-- 8. 用戶表 (認證用)
-- ============================================
CREATE TABLE IF NOT EXISTS users (
    id BIGSERIAL PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    name TEXT,
    email TEXT,
    role TEXT DEFAULT 'viewer',
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    last_login TIMESTAMP WITH TIME ZONE
);

COMMENT ON TABLE users IS '用戶認證表';
COMMENT ON COLUMN users.role IS '角色: admin, editor, viewer';

-- 用戶索引
CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);
CREATE INDEX IF NOT EXISTS idx_users_active ON users(is_active) WHERE is_active = TRUE;

-- ============================================
-- 9. 版本記錄表
-- ============================================
CREATE TABLE IF NOT EXISTS schema_migrations (
    version TEXT PRIMARY KEY,
    applied_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

INSERT INTO schema_migrations (version) VALUES ('001') ON CONFLICT DO NOTHING;

-- ============================================
-- 完成訊息
-- ============================================
DO $$
BEGIN
    RAISE NOTICE 'Schema 001 初始化完成';
    RAISE NOTICE '表格數量: 9';
    RAISE NOTICE '- news, watchlist, daily_prices, fundamentals';
    RAISE NOTICE '- macro_indicators, macro_data, market_cycles';
    RAISE NOTICE '- users, schema_migrations';
END $$;
