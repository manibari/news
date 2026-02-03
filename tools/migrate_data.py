#!/usr/bin/env python3
"""
資料遷移工具 - SQLite 到 PostgreSQL

將現有 SQLite 資料遷移至 PostgreSQL

使用方式:
    python tools/migrate_data.py --source sqlite --target postgresql
    python tools/migrate_data.py --tables news --incremental
    python tools/migrate_data.py --dry-run

環境變數:
    DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD
"""

import argparse
import sys
from pathlib import Path
from datetime import datetime

# 將專案根目錄加入 Python path
sys.path.insert(0, str(Path(__file__).parent.parent))

# 嘗試載入 .env
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


def get_sqlite_client():
    """取得 SQLite 客戶端"""
    from src.data.sqlite_client import SQLiteClient
    return SQLiteClient()


def get_postgresql_client():
    """取得 PostgreSQL 客戶端"""
    from src.data.postgresql_client import PostgreSQLClient
    return PostgreSQLClient()


def migrate_news(source, target, batch_size=1000, dry_run=False):
    """遷移新聞資料"""
    print("\n📰 遷移新聞資料...")

    # 取得所有新聞
    offset = 0
    total_migrated = 0

    while True:
        news_list = source.get_news(limit=batch_size, offset=offset)
        if not news_list:
            break

        if dry_run:
            print(f"  [DRY RUN] 將遷移 {len(news_list)} 筆新聞 (offset={offset})")
        else:
            count = target.insert_news_bulk(news_list)
            total_migrated += count
            print(f"  已遷移 {count} 筆新聞 (offset={offset})")

        offset += batch_size

    print(f"  ✅ 新聞遷移完成，共 {total_migrated} 筆")
    return total_migrated


def migrate_watchlist(source, target, dry_run=False):
    """遷移追蹤清單"""
    print("\n📊 遷移追蹤清單...")

    watchlist = source.get_watchlist(active_only=False)

    if dry_run:
        print(f"  [DRY RUN] 將遷移 {len(watchlist)} 檔股票")
        return len(watchlist)

    count = 0
    for item in watchlist:
        if target.add_to_watchlist(
            symbol=item["symbol"],
            name=item.get("name"),
            market=item.get("market"),
            sector=item.get("sector"),
            industry=item.get("industry")
        ):
            count += 1

    print(f"  ✅ 追蹤清單遷移完成，共 {count} 檔")
    return count


def migrate_daily_prices(source, target, batch_size=5000, dry_run=False):
    """遷移每日價格"""
    print("\n💹 遷移每日價格...")

    # 先取得所有股票代碼
    symbols = source.get_symbols()
    total_migrated = 0

    for symbol in symbols:
        prices = source.get_daily_prices(symbol)
        if not prices:
            continue

        if dry_run:
            print(f"  [DRY RUN] {symbol}: {len(prices)} 筆價格")
            total_migrated += len(prices)
        else:
            count = target.insert_daily_prices_bulk(prices)
            total_migrated += count
            print(f"  {symbol}: {count} 筆價格")

    print(f"  ✅ 價格遷移完成，共 {total_migrated} 筆")
    return total_migrated


def migrate_macro_indicators(source, target, dry_run=False):
    """遷移總經指標定義"""
    print("\n📈 遷移總經指標定義...")

    indicators = source.get_macro_indicators(active_only=False)

    if dry_run:
        print(f"  [DRY RUN] 將遷移 {len(indicators)} 個指標")
        return len(indicators)

    count = 0
    for indicator in indicators:
        if target.insert_macro_indicator(indicator):
            count += 1

    print(f"  ✅ 指標定義遷移完成，共 {count} 個")
    return count


def migrate_macro_data(source, target, dry_run=False):
    """遷移總經數據"""
    print("\n📉 遷移總經數據...")

    indicators = source.get_macro_indicators(active_only=False)
    total_migrated = 0

    for indicator in indicators:
        series_id = indicator["series_id"]
        data_list = source.get_macro_data(series_id)

        if not data_list:
            continue

        if dry_run:
            print(f"  [DRY RUN] {series_id}: {len(data_list)} 筆數據")
            total_migrated += len(data_list)
        else:
            count = target.insert_macro_data_bulk(series_id, data_list)
            total_migrated += count
            print(f"  {series_id}: {count} 筆數據")

    print(f"  ✅ 總經數據遷移完成，共 {total_migrated} 筆")
    return total_migrated


def migrate_market_cycles(source, target, dry_run=False):
    """遷移市場週期"""
    print("\n🔄 遷移市場週期...")

    # 取得最新週期（目前 API 只支援取最新一筆）
    latest = source.get_latest_cycle()

    if not latest:
        print("  ⚠️ 無市場週期資料")
        return 0

    if dry_run:
        print(f"  [DRY RUN] 將遷移週期: {latest.get('date')} - {latest.get('phase')}")
        return 1

    if target.insert_market_cycle(latest):
        print(f"  ✅ 市場週期遷移完成: {latest.get('date')} - {latest.get('phase')}")
        return 1

    return 0


def main():
    parser = argparse.ArgumentParser(
        description="資料遷移工具 - SQLite 到 PostgreSQL",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
範例:
  python tools/migrate_data.py                    # 遷移所有資料
  python tools/migrate_data.py --tables news     # 只遷移新聞
  python tools/migrate_data.py --dry-run         # 模擬執行，不實際寫入
  python tools/migrate_data.py --tables news,watchlist  # 遷移多個表

可用的表格:
  news, watchlist, daily_prices, macro_indicators, macro_data, market_cycles
        """
    )

    parser.add_argument(
        "--source",
        type=str,
        default="sqlite",
        choices=["sqlite"],
        help="來源資料庫 (預設: sqlite)"
    )
    parser.add_argument(
        "--target",
        type=str,
        default="postgresql",
        choices=["postgresql", "supabase"],
        help="目標資料庫 (預設: postgresql)"
    )
    parser.add_argument(
        "--tables",
        type=str,
        default="all",
        help="要遷移的表格，以逗號分隔 (預設: all)"
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=1000,
        help="批次大小 (預設: 1000)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="模擬執行，不實際寫入"
    )

    args = parser.parse_args()

    print("=" * 60)
    print("資料遷移工具")
    print("=" * 60)
    print(f"來源: {args.source}")
    print(f"目標: {args.target}")
    print(f"表格: {args.tables}")
    print(f"批次大小: {args.batch_size}")
    print(f"模擬執行: {args.dry_run}")
    print("=" * 60)

    # 建立客戶端
    try:
        source = get_sqlite_client()
        print("✅ SQLite 來源連線成功")
    except Exception as e:
        print(f"❌ SQLite 連線失敗: {e}")
        sys.exit(1)

    try:
        if args.target == "postgresql":
            target = get_postgresql_client()
        else:
            from src.data.supabase_client import SupabaseClient
            target = SupabaseClient()
        print(f"✅ {args.target} 目標連線成功")
    except Exception as e:
        print(f"❌ {args.target} 連線失敗: {e}")
        sys.exit(1)

    # 決定要遷移的表格
    all_tables = [
        "news", "watchlist", "daily_prices",
        "macro_indicators", "macro_data", "market_cycles"
    ]

    if args.tables == "all":
        tables = all_tables
    else:
        tables = [t.strip() for t in args.tables.split(",")]

    # 執行遷移
    start_time = datetime.now()
    results = {}

    for table in tables:
        if table == "news":
            results["news"] = migrate_news(source, target, args.batch_size, args.dry_run)
        elif table == "watchlist":
            results["watchlist"] = migrate_watchlist(source, target, args.dry_run)
        elif table == "daily_prices":
            results["daily_prices"] = migrate_daily_prices(source, target, args.batch_size, args.dry_run)
        elif table == "macro_indicators":
            results["macro_indicators"] = migrate_macro_indicators(source, target, args.dry_run)
        elif table == "macro_data":
            results["macro_data"] = migrate_macro_data(source, target, args.dry_run)
        elif table == "market_cycles":
            results["market_cycles"] = migrate_market_cycles(source, target, args.dry_run)
        else:
            print(f"⚠️ 未知的表格: {table}")

    # 總結
    end_time = datetime.now()
    duration = (end_time - start_time).total_seconds()

    print("\n" + "=" * 60)
    print("遷移完成總結")
    print("=" * 60)

    for table, count in results.items():
        print(f"  {table}: {count} 筆")

    print(f"\n總耗時: {duration:.2f} 秒")

    if args.dry_run:
        print("\n⚠️ 這是模擬執行，實際資料未被修改")
        print("移除 --dry-run 參數以執行實際遷移")


if __name__ == "__main__":
    main()
