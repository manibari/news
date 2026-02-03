"""
資料存取層

提供統一的資料存取介面，支援多種資料庫後端：
- SQLite (本地開發)
- PostgreSQL (地端正式環境)
- Supabase (雲端)

透過 DB_TYPE 環境變數選擇後端
"""

import os

from .base import DataClient

# 嘗試載入 .env
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

__all__ = ["DataClient", "get_client", "reset_client"]


_client = None


def get_client(db_type: str = None) -> DataClient:
    """
    取得資料客戶端（單例模式）

    Args:
        db_type: 資料庫類型，可選值：
            - "sqlite": 使用本地 SQLite
            - "postgresql": 使用地端 PostgreSQL
            - "supabase": 使用雲端 Supabase
            - None: 從環境變數 DB_TYPE 讀取，預設 sqlite

    Returns:
        DataClient: 資料客戶端實例
    """
    global _client

    if _client is not None:
        return _client

    # 決定使用哪種資料庫
    db_type = db_type or os.getenv("DB_TYPE", "sqlite").lower()

    if db_type == "postgresql":
        _client = _create_postgresql_client()
    elif db_type == "supabase":
        _client = _create_supabase_client()
    else:
        _client = _create_sqlite_client()

    return _client


def _create_sqlite_client() -> DataClient:
    """建立 SQLite 客戶端"""
    from .sqlite_client import SQLiteClient
    print("📁 使用 SQLite 資料庫")
    return SQLiteClient()


def _create_postgresql_client() -> DataClient:
    """建立 PostgreSQL 客戶端"""
    try:
        from .postgresql_client import PostgreSQLClient
        client = PostgreSQLClient()
        # 測試連線
        client.get_stats()
        print(f"🐘 使用 PostgreSQL 資料庫 ({os.getenv('DB_HOST', 'localhost')}:{os.getenv('DB_PORT', '5432')})")
        return client
    except ImportError:
        print("⚠️ psycopg2 未安裝，請執行: pip install psycopg2-binary")
        print("📁 降級為 SQLite")
        return _create_sqlite_client()
    except Exception as e:
        print(f"⚠️ PostgreSQL 連線失敗: {e}")
        print("📁 降級為 SQLite")
        return _create_sqlite_client()


def _create_supabase_client() -> DataClient:
    """建立 Supabase 客戶端"""
    try:
        from .supabase_client import SupabaseClient
        from config.supabase_config import SUPABASE_URL, SUPABASE_KEY

        if not SUPABASE_KEY:
            raise ValueError("SUPABASE_KEY 未設定")

        client = SupabaseClient()
        # 測試連線
        client.get_news(limit=1)
        print("☁️ 使用 Supabase 資料庫")
        return client
    except ImportError:
        print("⚠️ supabase 未安裝或設定檔不存在")
        print("📁 降級為 SQLite")
        return _create_sqlite_client()
    except Exception as e:
        print(f"⚠️ Supabase 連線失敗: {e}")
        print("📁 降級為 SQLite")
        return _create_sqlite_client()


def reset_client():
    """重設客戶端（用於測試或切換資料庫）"""
    global _client
    _client = None


def get_client_info() -> dict:
    """取得目前使用的資料庫資訊"""
    global _client

    if _client is None:
        return {"status": "not_initialized", "type": None}

    client_type = type(_client).__name__
    db_type_map = {
        "SQLiteClient": "sqlite",
        "PostgreSQLClient": "postgresql",
        "SupabaseClient": "supabase"
    }

    return {
        "status": "connected",
        "type": db_type_map.get(client_type, "unknown"),
        "client_class": client_type
    }
