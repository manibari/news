#!/usr/bin/env python3
"""
PostgreSQL 連接測試腳本

在不影響現有 Streamlit 應用的前提下，測試 PostgreSQL 連接和資料操作。

使用方式:
    python tools/test_postgresql.py              # 執行所有測試
    python tools/test_postgresql.py --quick      # 只測試連線
    python tools/test_postgresql.py --read       # 測試讀取操作
    python tools/test_postgresql.py --write      # 測試寫入操作
    python tools/test_postgresql.py --compare    # 比較 SQLite 和 PostgreSQL 資料

環境變數:
    DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD
"""

import argparse
import sys
import os
from pathlib import Path
from datetime import datetime, date, timedelta

# 將專案根目錄加入 Python path
sys.path.insert(0, str(Path(__file__).parent.parent))

# 嘗試載入 .env
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


class TestResult:
    """測試結果"""
    def __init__(self, name: str):
        self.name = name
        self.passed = False
        self.message = ""
        self.details = {}

    def __str__(self):
        status = "✅ PASS" if self.passed else "❌ FAIL"
        return f"{status} {self.name}: {self.message}"


def test_connection() -> TestResult:
    """測試 PostgreSQL 連線"""
    result = TestResult("PostgreSQL 連線測試")

    try:
        from src.data.postgresql_client import PostgreSQLClient

        config = {
            'host': os.getenv('DB_HOST', 'localhost'),
            'port': os.getenv('DB_PORT', '5432'),
            'database': os.getenv('DB_NAME', 'stock_analysis'),
        }

        client = PostgreSQLClient()
        stats = client.get_stats()

        result.passed = True
        result.message = f"連線成功 ({config['host']}:{config['port']}/{config['database']})"
        result.details = stats

    except ImportError as e:
        result.message = f"缺少依賴: {e}"
    except Exception as e:
        result.message = f"連線失敗: {e}"

    return result


def test_read_news() -> TestResult:
    """測試讀取新聞"""
    result = TestResult("讀取新聞測試")

    try:
        from src.data.postgresql_client import PostgreSQLClient
        client = PostgreSQLClient()

        # 測試取得新聞
        news = client.get_news(limit=10)
        count = client.get_news_count()
        sources = client.get_news_sources()

        result.passed = True
        result.message = f"成功讀取 {len(news)} 則新聞，資料庫共 {count} 則"
        result.details = {
            "count": count,
            "sources": sources[:5] if sources else [],
            "sample_titles": [n.get("title", "")[:50] for n in news[:3]]
        }

    except Exception as e:
        result.message = f"讀取失敗: {e}"

    return result


def test_read_watchlist() -> TestResult:
    """測試讀取追蹤清單"""
    result = TestResult("讀取追蹤清單測試")

    try:
        from src.data.postgresql_client import PostgreSQLClient
        client = PostgreSQLClient()

        watchlist = client.get_watchlist()
        symbols = client.get_symbols()

        result.passed = True
        result.message = f"成功讀取 {len(watchlist)} 檔股票"
        result.details = {
            "count": len(watchlist),
            "sample_symbols": symbols[:10] if symbols else []
        }

    except Exception as e:
        result.message = f"讀取失敗: {e}"

    return result


def test_read_prices() -> TestResult:
    """測試讀取價格數據"""
    result = TestResult("讀取價格數據測試")

    try:
        from src.data.postgresql_client import PostgreSQLClient
        client = PostgreSQLClient()

        # 先取得一個股票代碼
        symbols = client.get_symbols()
        if not symbols:
            result.message = "無股票資料"
            return result

        symbol = symbols[0]
        prices = client.get_daily_prices(symbol, limit=10)
        latest = client.get_latest_price(symbol)
        stats = client.get_price_stats()

        result.passed = True
        result.message = f"{symbol} 成功讀取 {len(prices)} 筆價格"
        result.details = {
            "symbol": symbol,
            "price_count": len(prices),
            "latest": latest,
            "stats": stats
        }

    except Exception as e:
        result.message = f"讀取失敗: {e}"

    return result


def test_read_macro() -> TestResult:
    """測試讀取總經數據"""
    result = TestResult("讀取總經數據測試")

    try:
        from src.data.postgresql_client import PostgreSQLClient
        client = PostgreSQLClient()

        indicators = client.get_macro_indicators()
        cycle = client.get_latest_cycle()

        if not indicators:
            result.message = "無總經指標資料"
            return result

        # 測試讀取第一個指標的數據
        series_id = indicators[0].get("series_id")
        data = client.get_macro_data(series_id) if series_id else []

        result.passed = True
        result.message = f"成功讀取 {len(indicators)} 個指標"
        result.details = {
            "indicators": [i.get("series_id") for i in indicators],
            "latest_cycle": cycle,
            "sample_data_count": len(data) if data else 0
        }

    except Exception as e:
        result.message = f"讀取失敗: {e}"

    return result


def test_write_news() -> TestResult:
    """測試寫入新聞（使用測試資料）"""
    result = TestResult("寫入新聞測試")

    try:
        from src.data.postgresql_client import PostgreSQLClient
        client = PostgreSQLClient()

        # 建立測試新聞
        test_news = {
            "title": f"[TEST] PostgreSQL 連線測試 - {datetime.now().isoformat()}",
            "content": "這是一則測試新聞，用於驗證 PostgreSQL 寫入功能。",
            "url": f"https://test.example.com/test-{datetime.now().timestamp()}",
            "source": "Test",
            "category": "測試",
            "published_at": datetime.now().isoformat(),
            "source_type": "test"
        }

        # 測試單筆插入
        success = client.insert_news(test_news)

        if success:
            result.passed = True
            result.message = "成功寫入測試新聞"
            result.details = {"inserted": test_news["title"]}
        else:
            result.message = "寫入返回 False（可能是重複）"

    except Exception as e:
        result.message = f"寫入失敗: {e}"

    return result


def test_compare_sqlite_postgresql() -> TestResult:
    """比較 SQLite 和 PostgreSQL 的資料"""
    result = TestResult("SQLite vs PostgreSQL 資料比較")

    try:
        from src.data.sqlite_client import SQLiteClient
        from src.data.postgresql_client import PostgreSQLClient

        sqlite = SQLiteClient()
        pg = PostgreSQLClient()

        sqlite_stats = sqlite.get_stats()
        pg_stats = pg.get_stats()

        comparison = {
            "news": {
                "sqlite": sqlite_stats.get("news_count", 0),
                "postgresql": pg_stats.get("news_count", 0)
            },
            "watchlist": {
                "sqlite": sqlite_stats.get("watchlist_count", 0),
                "postgresql": pg_stats.get("watchlist_count", 0)
            },
            "prices": {
                "sqlite": sqlite_stats.get("prices_count", 0),
                "postgresql": pg_stats.get("prices_count", 0)
            }
        }

        # 檢查是否需要遷移
        needs_migration = any(
            comparison[k]["sqlite"] > comparison[k]["postgresql"]
            for k in comparison
        )

        result.passed = True
        result.message = "需要遷移" if needs_migration else "資料已同步"
        result.details = comparison

    except Exception as e:
        result.message = f"比較失敗: {e}"

    return result


def run_all_tests(quick=False, read_only=False, write_only=False, compare_only=False):
    """執行所有測試"""
    print("=" * 60)
    print("PostgreSQL 連接測試")
    print("=" * 60)
    print(f"時間: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"DB_HOST: {os.getenv('DB_HOST', 'localhost')}")
    print(f"DB_PORT: {os.getenv('DB_PORT', '5432')}")
    print(f"DB_NAME: {os.getenv('DB_NAME', 'stock_analysis')}")
    print("=" * 60)

    results = []

    # 連線測試（必做）
    print("\n[1/7] 連線測試...")
    conn_result = test_connection()
    results.append(conn_result)
    print(conn_result)

    if not conn_result.passed:
        print("\n❌ 連線失敗，無法繼續測試")
        print("\n請檢查:")
        print("  1. PostgreSQL 是否已啟動")
        print("  2. .env 中的連線設定是否正確")
        print("  3. 資料庫是否已建立 (python -m migrations.init_schema --create-db)")
        return results

    if quick:
        print("\n✅ 快速測試完成")
        return results

    if compare_only:
        print("\n[2/7] 資料比較...")
        results.append(test_compare_sqlite_postgresql())
        print(results[-1])
        return results

    if not write_only:
        # 讀取測試
        print("\n[2/7] 讀取新聞測試...")
        results.append(test_read_news())
        print(results[-1])

        print("\n[3/7] 讀取追蹤清單測試...")
        results.append(test_read_watchlist())
        print(results[-1])

        print("\n[4/7] 讀取價格數據測試...")
        results.append(test_read_prices())
        print(results[-1])

        print("\n[5/7] 讀取總經數據測試...")
        results.append(test_read_macro())
        print(results[-1])

    if not read_only:
        # 寫入測試
        print("\n[6/7] 寫入新聞測試...")
        results.append(test_write_news())
        print(results[-1])

        # 資料比較
        print("\n[7/7] 資料比較...")
        results.append(test_compare_sqlite_postgresql())
        print(results[-1])

    # 總結
    print("\n" + "=" * 60)
    print("測試總結")
    print("=" * 60)

    passed = sum(1 for r in results if r.passed)
    total = len(results)

    print(f"通過: {passed}/{total}")

    if passed == total:
        print("\n✅ 所有測試通過！PostgreSQL 連接正常。")
        print("\n下一步:")
        print("  1. 如需遷移資料: python tools/migrate_data.py")
        print("  2. 設定 .env: DB_TYPE=postgresql")
        print("  3. 重啟 Streamlit: streamlit run app.py")
    else:
        print("\n⚠️ 部分測試未通過，請檢查上方錯誤訊息。")

    return results


def main():
    parser = argparse.ArgumentParser(
        description="PostgreSQL 連接測試腳本",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )

    parser.add_argument("--quick", action="store_true", help="只測試連線")
    parser.add_argument("--read", action="store_true", help="只測試讀取操作")
    parser.add_argument("--write", action="store_true", help="只測試寫入操作")
    parser.add_argument("--compare", action="store_true", help="比較 SQLite 和 PostgreSQL")

    args = parser.parse_args()

    run_all_tests(
        quick=args.quick,
        read_only=args.read,
        write_only=args.write,
        compare_only=args.compare
    )


if __name__ == "__main__":
    main()
