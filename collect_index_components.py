#!/usr/bin/env python3
"""
收集 0050 成分股 和 NASDAQ 100 成分股的歷史數據
"""

import logging
import sys
from pathlib import Path
from datetime import date

sys.path.insert(0, str(Path(__file__).parent))

from src.finance.database import FinanceDatabase
from src.finance.collector import YFinanceCollector

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('index_components.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# 0050 成分股 (元大台灣50 ETF) - 2024年版本
# 台股代碼需加 .TW
TW50_COMPONENTS = [
    "2330.TW",  # 台積電
    "2454.TW",  # 聯發科
    "2317.TW",  # 鴻海
    "2308.TW",  # 台達電
    "2881.TW",  # 富邦金
    "2882.TW",  # 國泰金
    "2303.TW",  # 聯電
    "1303.TW",  # 南亞
    "2886.TW",  # 兆豐金
    "1301.TW",  # 台塑
    "2891.TW",  # 中信金
    "2412.TW",  # 中華電
    "3711.TW",  # 日月光投控
    "2884.TW",  # 玉山金
    "1326.TW",  # 台化
    "2002.TW",  # 中鋼
    "5880.TW",  # 合庫金
    "2885.TW",  # 元大金
    "2892.TW",  # 第一金
    "5871.TW",  # 中租-KY
    "2883.TW",  # 開發金
    "2379.TW",  # 瑞昱
    "3045.TW",  # 台灣大
    "2357.TW",  # 華碩
    "2382.TW",  # 廣達
    "1216.TW",  # 統一
    "2912.TW",  # 統一超
    "4904.TW",  # 遠傳
    "2887.TW",  # 台新金
    "6505.TW",  # 台塑化
    "3231.TW",  # 緯創
    "2880.TW",  # 華南金
    "2395.TW",  # 研華
    "2207.TW",  # 和泰車
    "1101.TW",  # 台泥
    "2890.TW",  # 永豐金
    "2327.TW",  # 國巨
    "3008.TW",  # 大立光
    "2301.TW",  # 光寶科
    "4938.TW",  # 和碩
    "2345.TW",  # 智邦
    "5876.TW",  # 上海商銀
    "2603.TW",  # 長榮
    "1102.TW",  # 亞泥
    "2609.TW",  # 陽明
    "9910.TW",  # 豐泰
    "2615.TW",  # 萬海
    "3034.TW",  # 聯詠
    "2408.TW",  # 南亞科
    "6669.TW",  # 緯穎
]

# NASDAQ 100 成分股 (2024年版本)
NASDAQ100_COMPONENTS = [
    "AAPL",   # Apple
    "MSFT",   # Microsoft
    "AMZN",   # Amazon
    "NVDA",   # NVIDIA
    "GOOGL",  # Alphabet Class A
    "GOOG",   # Alphabet Class C
    "META",   # Meta Platforms
    "TSLA",   # Tesla
    "AVGO",   # Broadcom
    "COST",   # Costco
    "ASML",   # ASML
    "PEP",    # PepsiCo
    "AZN",    # AstraZeneca
    "CSCO",   # Cisco
    "ADBE",   # Adobe
    "NFLX",   # Netflix
    "AMD",    # AMD
    "TMUS",   # T-Mobile
    "TXN",    # Texas Instruments
    "CMCSA",  # Comcast
    "QCOM",   # Qualcomm
    "INTC",   # Intel
    "INTU",   # Intuit
    "AMGN",   # Amgen
    "HON",    # Honeywell
    "AMAT",   # Applied Materials
    "ISRG",   # Intuitive Surgical
    "BKNG",   # Booking Holdings
    "VRTX",   # Vertex Pharmaceuticals
    "SBUX",   # Starbucks
    "LRCX",   # Lam Research
    "ADP",    # ADP
    "GILD",   # Gilead Sciences
    "MDLZ",   # Mondelez
    "ADI",    # Analog Devices
    "REGN",   # Regeneron
    "PANW",   # Palo Alto Networks
    "MU",     # Micron
    "KLAC",   # KLA Corporation
    "SNPS",   # Synopsys
    "CDNS",   # Cadence Design
    "MELI",   # MercadoLibre
    "PYPL",   # PayPal
    "CSX",    # CSX Corporation
    "CRWD",   # CrowdStrike
    "MAR",    # Marriott
    "ORLY",   # O'Reilly Auto
    "CTAS",   # Cintas
    "ABNB",   # Airbnb
    "NXPI",   # NXP Semiconductors
    "MRVL",   # Marvell Technology
    "PCAR",   # PACCAR
    "WDAY",   # Workday
    "CEG",    # Constellation Energy
    "FTNT",   # Fortinet
    "DASH",   # DoorDash
    "ROP",    # Roper Technologies
    "MNST",   # Monster Beverage
    "CPRT",   # Copart
    "PAYX",   # Paychex
    "AEP",    # American Electric Power
    "ODFL",   # Old Dominion Freight
    "ROST",   # Ross Stores
    "TTD",    # The Trade Desk
    "KDP",    # Keurig Dr Pepper
    "FAST",   # Fastenal
    "CHTR",   # Charter Communications
    "KHC",    # Kraft Heinz
    "DXCM",   # DexCom
    "VRSK",   # Verisk Analytics
    "EXC",    # Exelon
    "GEHC",   # GE HealthCare
    "IDXX",   # IDEXX Laboratories
    "EA",     # Electronic Arts
    "CTSH",   # Cognizant
    "XEL",    # Xcel Energy
    "CCEP",   # Coca-Cola Europacific
    "BKR",    # Baker Hughes
    "LULU",   # Lululemon
    "MCHP",   # Microchip Technology
    "FANG",   # Diamondback Energy
    "ANSS",   # ANSYS
    "ZS",     # Zscaler
    "TEAM",   # Atlassian
    "DDOG",   # Datadog
    "ON",     # ON Semiconductor
    "CDW",    # CDW Corporation
    "ILMN",   # Illumina
    "CSGP",   # CoStar Group
    "TTWO",   # Take-Two Interactive
    "GFS",    # GlobalFoundries
    "WBD",    # Warner Bros Discovery
    "BIIB",   # Biogen
    "DLTR",   # Dollar Tree
    "MDB",    # MongoDB
    "ARM",    # Arm Holdings
    "SMCI",   # Super Micro Computer
    "SPLK",   # Splunk (被收購，可能需移除)
    "MRNA",   # Moderna
]


def collect_index_components(
    collect_tw50: bool = True,
    collect_nasdaq100: bool = True,
    start_date: str = "2021-01-01"
):
    """
    收集指數成分股數據

    Args:
        collect_tw50: 是否收集0050成分股
        collect_nasdaq100: 是否收集NASDAQ 100成分股
        start_date: 歷史數據開始日期
    """
    db = FinanceDatabase()
    collector = YFinanceCollector(db)

    logger.info("=" * 60)
    logger.info("指數成分股數據收集")
    logger.info("=" * 60)

    symbols_to_collect = []

    # 0050 成分股
    if collect_tw50:
        logger.info(f"\n[Step 1] 新增 0050 成分股 ({len(TW50_COMPONENTS)} 檔)...")
        added = collector.add_symbols(TW50_COMPONENTS, market="TW", fetch_info=True)
        logger.info(f"  新增: {added} 檔")
        symbols_to_collect.extend(TW50_COMPONENTS)

    # NASDAQ 100 成分股
    if collect_nasdaq100:
        logger.info(f"\n[Step 2] 新增 NASDAQ 100 成分股 ({len(NASDAQ100_COMPONENTS)} 檔)...")
        added = collector.add_symbols(NASDAQ100_COMPONENTS, market="US", fetch_info=True)
        logger.info(f"  新增: {added} 檔")
        symbols_to_collect.extend(NASDAQ100_COMPONENTS)

    # 收集歷史價格數據
    if symbols_to_collect:
        logger.info(f"\n[Step 3] 收集歷史價格數據 ({start_date} ~ 今日)...")

        price_stats = collector.collect_historical_data(
            symbols=symbols_to_collect,
            start_date=start_date,
            end_date=date.today().strftime("%Y-%m-%d")
        )

        logger.info(f"\n價格數據收集完成:")
        logger.info(f"  收集: {price_stats['collected']} 筆")
        logger.info(f"  新增: {price_stats['inserted']} 筆")
        logger.info(f"  錯誤: {price_stats['errors']} 筆")

    # 顯示統計
    logger.info("\n" + "=" * 60)
    logger.info("資料庫統計")
    logger.info("=" * 60)

    stats = db.get_stats()
    logger.info(f"追蹤股票數: {stats['watchlist_count']}")
    logger.info(f"價格數據筆數: {stats['prices_count']}")

    if stats['by_market']:
        logger.info("\n按市場分類:")
        for market, count in stats['by_market'].items():
            logger.info(f"  {market}: {count} 檔")

    return stats


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="收集指數成分股數據")
    parser.add_argument("--tw50", action="store_true", help="只收集 0050 成分股")
    parser.add_argument("--nasdaq100", action="store_true", help="只收集 NASDAQ 100 成分股")
    parser.add_argument("--start", default="2021-01-01", help="歷史數據開始日期")

    args = parser.parse_args()

    # 如果沒有指定，則收集全部
    collect_tw50 = args.tw50 or (not args.tw50 and not args.nasdaq100)
    collect_nasdaq100 = args.nasdaq100 or (not args.tw50 and not args.nasdaq100)

    try:
        collect_index_components(
            collect_tw50=collect_tw50,
            collect_nasdaq100=collect_nasdaq100,
            start_date=args.start
        )
    except KeyboardInterrupt:
        logger.info("\n收集中斷")
    except Exception as e:
        logger.error(f"收集失敗: {e}")
        raise
