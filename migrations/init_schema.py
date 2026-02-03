"""
PostgreSQL Schema 初始化腳本

使用方式:
    python -m migrations.init_schema [--check]

參數:
    --check: 只檢查連線，不執行遷移

環境變數:
    DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD
"""

import os
import sys
import argparse
from pathlib import Path

# 嘗試載入 .env
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


def get_db_config():
    """從環境變數取得資料庫設定"""
    return {
        'host': os.getenv('DB_HOST', 'localhost'),
        'port': os.getenv('DB_PORT', '5432'),
        'database': os.getenv('DB_NAME', 'stock_analysis'),
        'user': os.getenv('DB_USER', 'postgres'),
        'password': os.getenv('DB_PASSWORD', ''),
    }


def check_connection(config):
    """檢查資料庫連線"""
    try:
        import psycopg2
    except ImportError:
        print("錯誤: 請先安裝 psycopg2")
        print("執行: pip install psycopg2-binary")
        return False

    try:
        conn = psycopg2.connect(**config)
        conn.close()
        print(f"✅ 連線成功: {config['host']}:{config['port']}/{config['database']}")
        return True
    except psycopg2.OperationalError as e:
        print(f"❌ 連線失敗: {e}")
        return False


def run_migration(config):
    """執行 Schema 遷移"""
    import psycopg2

    # 找到 SQL 檔案
    migrations_dir = Path(__file__).parent
    schema_file = migrations_dir / "001_init_schema.sql"

    if not schema_file.exists():
        print(f"錯誤: 找不到 {schema_file}")
        return False

    # 讀取 SQL
    with open(schema_file, 'r', encoding='utf-8') as f:
        sql = f.read()

    # 執行遷移
    try:
        conn = psycopg2.connect(**config)
        conn.autocommit = True
        cursor = conn.cursor()

        print(f"執行遷移: {schema_file.name}")
        cursor.execute(sql)

        # 檢查結果
        cursor.execute("SELECT version FROM schema_migrations ORDER BY applied_at DESC LIMIT 1")
        result = cursor.fetchone()

        if result:
            print(f"✅ 遷移完成，版本: {result[0]}")
        else:
            print("✅ 遷移完成")

        cursor.close()
        conn.close()
        return True

    except psycopg2.Error as e:
        print(f"❌ 遷移失敗: {e}")
        return False


def create_database(config):
    """建立資料庫 (如果不存在)"""
    import psycopg2
    from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

    db_name = config['database']
    admin_config = config.copy()
    admin_config['database'] = 'postgres'  # 連到預設資料庫

    try:
        conn = psycopg2.connect(**admin_config)
        conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        cursor = conn.cursor()

        # 檢查資料庫是否存在
        cursor.execute(
            "SELECT 1 FROM pg_catalog.pg_database WHERE datname = %s",
            (db_name,)
        )
        exists = cursor.fetchone()

        if not exists:
            print(f"建立資料庫: {db_name}")
            cursor.execute(f'CREATE DATABASE "{db_name}"')
            print(f"✅ 資料庫 {db_name} 建立成功")
        else:
            print(f"資料庫 {db_name} 已存在")

        cursor.close()
        conn.close()
        return True

    except psycopg2.Error as e:
        print(f"❌ 建立資料庫失敗: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description='PostgreSQL Schema 初始化')
    parser.add_argument('--check', action='store_true', help='只檢查連線')
    parser.add_argument('--create-db', action='store_true', help='建立資料庫')
    args = parser.parse_args()

    config = get_db_config()

    print("=" * 50)
    print("PostgreSQL Schema 初始化")
    print("=" * 50)
    print(f"主機: {config['host']}:{config['port']}")
    print(f"資料庫: {config['database']}")
    print(f"用戶: {config['user']}")
    print("=" * 50)

    if args.create_db:
        if not create_database(config):
            sys.exit(1)

    if args.check:
        success = check_connection(config)
        sys.exit(0 if success else 1)

    # 檢查連線
    if not check_connection(config):
        print("\n提示: 如果資料庫不存在，請先執行:")
        print("  python -m migrations.init_schema --create-db")
        sys.exit(1)

    # 執行遷移
    if run_migration(config):
        print("\n下一步:")
        print("  1. 執行資料遷移: python tools/migrate_data.py")
        print("  2. 設定 .env 中的 DB_TYPE=postgresql")
        print("  3. 啟動應用: streamlit run app.py")
    else:
        sys.exit(1)


if __name__ == '__main__':
    main()
