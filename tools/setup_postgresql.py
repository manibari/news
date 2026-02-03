#!/usr/bin/env python3
"""
PostgreSQL 自動設定腳本

一鍵完成 PostgreSQL 設定：
1. 檢查連線
2. 建立資料庫（如需要）
3. 執行 Schema 遷移
4. 遷移 SQLite 資料
5. 驗證資料完整性

使用方式:
    python tools/setup_postgresql.py              # 完整設定流程
    python tools/setup_postgresql.py --check      # 只檢查狀態
    python tools/setup_postgresql.py --skip-data  # 跳過資料遷移
"""

import argparse
import subprocess
import sys
import os
from pathlib import Path
from datetime import datetime

# 將專案根目錄加入 Python path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# 嘗試載入 .env
try:
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")
except ImportError:
    pass


def print_header(title: str):
    """印出標題"""
    print("\n" + "=" * 60)
    print(f"  {title}")
    print("=" * 60)


def print_step(step: int, total: int, description: str):
    """印出步驟"""
    print(f"\n[{step}/{total}] {description}")
    print("-" * 40)


def check_dependencies() -> bool:
    """檢查必要的依賴"""
    print_step(1, 6, "檢查依賴套件")

    dependencies = {
        "psycopg2": "psycopg2-binary",
        "dotenv": "python-dotenv"
    }

    missing = []

    for module, package in dependencies.items():
        try:
            __import__(module)
            print(f"  ✅ {module}")
        except ImportError:
            print(f"  ❌ {module} (pip install {package})")
            missing.append(package)

    if missing:
        print(f"\n請先安裝缺少的套件:")
        print(f"  pip install {' '.join(missing)}")
        return False

    return True


def check_env_config() -> dict:
    """檢查環境變數設定"""
    print_step(2, 6, "檢查環境變數")

    config = {
        'host': os.getenv('DB_HOST', 'localhost'),
        'port': os.getenv('DB_PORT', '5432'),
        'database': os.getenv('DB_NAME', 'stock_analysis'),
        'user': os.getenv('DB_USER', 'postgres'),
        'password': os.getenv('DB_PASSWORD', ''),
    }

    print(f"  DB_HOST: {config['host']}")
    print(f"  DB_PORT: {config['port']}")
    print(f"  DB_NAME: {config['database']}")
    print(f"  DB_USER: {config['user']}")
    print(f"  DB_PASSWORD: {'*' * len(config['password']) if config['password'] else '(未設定)'}")

    env_file = PROJECT_ROOT / ".env"
    if not env_file.exists():
        print(f"\n  ⚠️ .env 檔案不存在")
        print(f"  請執行: cp .env.example .env")

    return config


def check_postgresql_connection(config: dict) -> bool:
    """檢查 PostgreSQL 連線"""
    print_step(3, 6, "測試 PostgreSQL 連線")

    try:
        import psycopg2

        # 先嘗試連到 postgres 資料庫（檢查伺服器是否運行）
        test_config = config.copy()
        test_config['database'] = 'postgres'

        conn = psycopg2.connect(**test_config)
        conn.close()
        print(f"  ✅ PostgreSQL 伺服器運行中")

        # 檢查目標資料庫是否存在
        conn = psycopg2.connect(**test_config)
        conn.autocommit = True
        cursor = conn.cursor()
        cursor.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s",
            (config['database'],)
        )
        db_exists = cursor.fetchone() is not None
        cursor.close()
        conn.close()

        if db_exists:
            print(f"  ✅ 資料庫 '{config['database']}' 已存在")
        else:
            print(f"  ⚠️ 資料庫 '{config['database']}' 不存在")

        return True, db_exists

    except psycopg2.OperationalError as e:
        print(f"  ❌ 連線失敗: {e}")
        print("\n  請確認:")
        print("    1. PostgreSQL 是否已安裝並啟動")
        print("    2. 連線設定是否正確")
        print("    3. 防火牆是否允許連線")
        return False, False


def create_database(config: dict) -> bool:
    """建立資料庫"""
    print("\n  建立資料庫...")

    try:
        import psycopg2
        from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

        admin_config = config.copy()
        admin_config['database'] = 'postgres'

        conn = psycopg2.connect(**admin_config)
        conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        cursor = conn.cursor()
        cursor.execute(f'CREATE DATABASE "{config["database"]}"')
        cursor.close()
        conn.close()

        print(f"  ✅ 資料庫 '{config['database']}' 建立成功")
        return True

    except psycopg2.Error as e:
        print(f"  ❌ 建立資料庫失敗: {e}")
        return False


def run_schema_migration(config: dict) -> bool:
    """執行 Schema 遷移"""
    print_step(4, 6, "執行 Schema 遷移")

    try:
        import psycopg2

        # 讀取 SQL 檔案
        schema_file = PROJECT_ROOT / "migrations" / "001_init_schema.sql"
        if not schema_file.exists():
            print(f"  ❌ 找不到 {schema_file}")
            return False

        with open(schema_file, 'r', encoding='utf-8') as f:
            sql = f.read()

        # 執行 SQL
        conn = psycopg2.connect(**config)
        conn.autocommit = True
        cursor = conn.cursor()

        print(f"  執行 {schema_file.name}...")
        cursor.execute(sql)

        # 檢查結果
        cursor.execute("SELECT version FROM schema_migrations ORDER BY applied_at DESC LIMIT 1")
        result = cursor.fetchone()

        cursor.close()
        conn.close()

        if result:
            print(f"  ✅ Schema 遷移完成，版本: {result[0]}")
            return True
        else:
            print("  ✅ Schema 遷移完成")
            return True

    except psycopg2.Error as e:
        print(f"  ❌ Schema 遷移失敗: {e}")
        return False


def migrate_data() -> bool:
    """遷移 SQLite 資料"""
    print_step(5, 6, "遷移 SQLite 資料到 PostgreSQL")

    try:
        from src.data.sqlite_client import SQLiteClient
        from src.data.postgresql_client import PostgreSQLClient

        sqlite = SQLiteClient()
        pg = PostgreSQLClient()

        sqlite_stats = sqlite.get_stats()
        pg_stats = pg.get_stats()

        print(f"\n  SQLite 資料:")
        print(f"    新聞: {sqlite_stats.get('news_count', 0)} 筆")
        print(f"    股票: {sqlite_stats.get('watchlist_count', 0)} 檔")
        print(f"    價格: {sqlite_stats.get('prices_count', 0)} 筆")

        print(f"\n  PostgreSQL 資料:")
        print(f"    新聞: {pg_stats.get('news_count', 0)} 筆")
        print(f"    股票: {pg_stats.get('watchlist_count', 0)} 檔")
        print(f"    價格: {pg_stats.get('prices_count', 0)} 筆")

        # 檢查是否需要遷移
        needs_migration = (
            sqlite_stats.get('news_count', 0) > pg_stats.get('news_count', 0) or
            sqlite_stats.get('watchlist_count', 0) > pg_stats.get('watchlist_count', 0) or
            sqlite_stats.get('prices_count', 0) > pg_stats.get('prices_count', 0)
        )

        if not needs_migration:
            print("\n  ✅ 資料已同步，無需遷移")
            return True

        print("\n  開始遷移...")

        # 遷移追蹤清單
        if sqlite_stats.get('watchlist_count', 0) > pg_stats.get('watchlist_count', 0):
            watchlist = sqlite.get_watchlist(active_only=False)
            count = 0
            for item in watchlist:
                if pg.add_to_watchlist(
                    symbol=item["symbol"],
                    name=item.get("name"),
                    market=item.get("market"),
                    sector=item.get("sector"),
                    industry=item.get("industry")
                ):
                    count += 1
            print(f"    追蹤清單: {count} 檔")

        # 遷移新聞
        if sqlite_stats.get('news_count', 0) > pg_stats.get('news_count', 0):
            offset = 0
            batch_size = 1000
            total = 0
            while True:
                news = sqlite.get_news(limit=batch_size, offset=offset)
                if not news:
                    break
                count = pg.insert_news_bulk(news)
                total += count
                offset += batch_size
                print(f"    新聞: {total} 筆...", end='\r')
            print(f"    新聞: {total} 筆        ")

        # 遷移價格
        if sqlite_stats.get('prices_count', 0) > pg_stats.get('prices_count', 0):
            symbols = sqlite.get_symbols()
            total = 0
            for symbol in symbols:
                prices = sqlite.get_daily_prices(symbol)
                if prices:
                    count = pg.insert_daily_prices_bulk(prices)
                    total += count
            print(f"    價格: {total} 筆")

        print("\n  ✅ 資料遷移完成")
        return True

    except Exception as e:
        print(f"  ❌ 資料遷移失敗: {e}")
        import traceback
        traceback.print_exc()
        return False


def verify_setup() -> bool:
    """驗證設定"""
    print_step(6, 6, "驗證設定")

    try:
        from src.data.postgresql_client import PostgreSQLClient
        client = PostgreSQLClient()

        stats = client.get_stats()

        print(f"  PostgreSQL 資料統計:")
        print(f"    新聞: {stats.get('news_count', 0)} 筆")
        print(f"    股票: {stats.get('watchlist_count', 0)} 檔")
        print(f"    價格: {stats.get('prices_count', 0)} 筆")

        # 測試讀取
        news = client.get_news(limit=1)
        if news:
            print(f"  ✅ 新聞讀取正常")

        watchlist = client.get_watchlist()
        if watchlist:
            print(f"  ✅ 追蹤清單讀取正常")

        print("\n  ✅ PostgreSQL 設定驗證完成！")
        return True

    except Exception as e:
        print(f"  ❌ 驗證失敗: {e}")
        return False


def print_next_steps():
    """印出下一步指示"""
    print_header("設定完成！下一步")

    print("""
1. 設定環境變數以使用 PostgreSQL:

   編輯 .env 檔案:
   DB_TYPE=postgresql

2. 重啟 Streamlit:

   streamlit run app.py

3. 如需切回 SQLite:

   DB_TYPE=sqlite

4. 測試 PostgreSQL 連線:

   python tools/test_postgresql.py
""")


def main():
    parser = argparse.ArgumentParser(
        description="PostgreSQL 自動設定腳本",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )

    parser.add_argument("--check", action="store_true", help="只檢查狀態，不執行設定")
    parser.add_argument("--skip-data", action="store_true", help="跳過資料遷移")
    parser.add_argument("--force", action="store_true", help="強制重新執行所有步驟")

    args = parser.parse_args()

    print_header("PostgreSQL 自動設定")
    print(f"時間: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    # 步驟 1: 檢查依賴
    if not check_dependencies():
        sys.exit(1)

    # 步驟 2: 檢查環境變數
    config = check_env_config()

    # 步驟 3: 測試連線
    connected, db_exists = check_postgresql_connection(config)

    if not connected:
        sys.exit(1)

    if args.check:
        print("\n✅ 檢查完成")
        sys.exit(0)

    # 建立資料庫（如需要）
    if not db_exists:
        if not create_database(config):
            sys.exit(1)

    # 步驟 4: Schema 遷移
    if not run_schema_migration(config):
        sys.exit(1)

    # 步驟 5: 資料遷移
    if not args.skip_data:
        if not migrate_data():
            sys.exit(1)
    else:
        print_step(5, 6, "跳過資料遷移")
        print("  (使用 --skip-data 參數)")

    # 步驟 6: 驗證
    if not verify_setup():
        sys.exit(1)

    # 完成
    print_next_steps()


if __name__ == "__main__":
    main()
