"""
週期策略選擇器

根據市場週期階段推薦適合的投資策略，
整合基本面分析篩選符合條件的個股。

評分優先順序：
1. 市場趨勢（週期契合度）- 30%
2. 稀缺性（護城河/競爭優勢）- 30%
3. 未來發展性（成長潛力）- 25%
4. 動能（技術分析）- 15%
"""

import logging
import sqlite3
from datetime import datetime, date
from typing import Dict, List, Optional, Tuple
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from config.macro_indicators import CYCLE_STRATEGIES, MARKET_CYCLES
from src.finance.macro_database import MacroDatabase
from src.finance.cycle_analyzer import MarketCycleAnalyzer
from src.finance.analyzer import TechnicalAnalyzer


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# 評分權重配置
SCORING_WEIGHTS = {
    "cycle_fit": 0.30,      # 市場趨勢/週期契合度
    "moat": 0.30,           # 稀缺性/護城河
    "growth": 0.25,         # 未來發展性
    "momentum": 0.15,       # 動能（技術分析）
}

# 板塊映射 (sector name -> keywords for matching)
SECTOR_MAPPING = {
    "Technology": ["Technology", "Tech", "Software", "Semiconductor", "IT"],
    "Consumer Discretionary": ["Consumer Discretionary", "Retail", "Auto", "Apparel", "Consumer Cyclical"],
    "Financials": ["Financial", "Banks", "Insurance", "Asset Management"],
    "Healthcare": ["Healthcare", "Biotech", "Pharma", "Medical"],
    "Consumer Staples": ["Consumer Staples", "Food", "Beverage", "Household"],
    "Utilities": ["Utilities", "Electric", "Gas", "Water"],
    "Industrials": ["Industrial", "Aerospace", "Defense", "Machinery"],
    "Materials": ["Materials", "Mining", "Chemicals", "Metals"],
    "Energy": ["Energy", "Oil", "Gas", "Petroleum"],
    "Real Estate": ["Real Estate", "REIT"],
    "Communication Services": ["Communication", "Media", "Telecom", "Entertainment"]
}

# 稀缺性評估標準（護城河指標）
MOAT_THRESHOLDS = {
    "profit_margin": {
        "excellent": 20,    # > 20% 利潤率 = 極佳定價能力
        "good": 10,         # > 10% = 良好
        "poor": 5           # < 5% = 較弱
    },
    "operating_margin": {
        "excellent": 25,
        "good": 15,
        "poor": 8
    },
    "roe": {
        "excellent": 20,    # > 20% ROE = 極佳資本效率
        "good": 12,
        "poor": 8
    },
    "held_by_institutions": {
        "excellent": 70,    # > 70% 機構持股 = 高度認可
        "good": 50,
        "poor": 30
    }
}

# 成長性評估標準（財務面）
GROWTH_THRESHOLDS = {
    "peg_ratio": {
        "excellent": 1.0,   # PEG < 1 = 成長被低估
        "good": 1.5,
        "overvalued": 2.0   # PEG > 2 = 可能高估
    },
    "forward_pe_discount": {
        "excellent": 20,    # Forward PE 比 Current PE 低 20%+ = 高成長預期
        "good": 10,
        "poor": 0
    }
}

# 市場趨勢主題 - 未來發展性評估（產業面）
# 分數代表該主題的市場發展潛力 (0-1)
MARKET_MEGATRENDS = {
    # AI/人工智慧生態系 - 最熱門趨勢
    "ai": {
        "score": 1.0,
        "keywords": ["ai", "artificial intelligence", "machine learning", "gpu", "data center",
                     "nvidia", "semiconductor", "chip", "generative"],
        "description": "AI 革命 - 算力需求爆發"
    },
    # 電動車/自動駕駛
    "ev": {
        "score": 0.85,
        "keywords": ["electric vehicle", "ev", "tesla", "battery", "lithium",
                     "autonomous", "self-driving", "charging"],
        "description": "電動車轉型 - 能源革命"
    },
    # 再生能源/綠能
    "clean_energy": {
        "score": 0.80,
        "keywords": ["solar", "wind", "renewable", "clean energy", "green",
                     "hydrogen", "carbon neutral", "esg"],
        "description": "綠能轉型 - 政策支持"
    },
    # 生技醫療/精準醫療
    "biotech": {
        "score": 0.75,
        "keywords": ["biotech", "gene", "mrna", "therapy", "drug discovery",
                     "precision medicine", "clinical trial"],
        "description": "精準醫療 - 人口老化受惠"
    },
    # 雲端/數位轉型
    "cloud": {
        "score": 0.70,
        "keywords": ["cloud", "saas", "aws", "azure", "digital transformation",
                     "cybersecurity", "software"],
        "description": "數位轉型持續"
    },
    # 國防/航太
    "defense": {
        "score": 0.65,
        "keywords": ["defense", "aerospace", "military", "satellite", "space"],
        "description": "地緣政治風險下受惠"
    },
    # 基礎建設
    "infrastructure": {
        "score": 0.60,
        "keywords": ["infrastructure", "construction", "5g", "telecom"],
        "description": "政府基建投資"
    },
}

# 衰退中的產業趨勢（負面）
DECLINING_TRENDS = {
    "traditional_retail": {
        "penalty": -0.3,
        "keywords": ["mall", "department store", "brick and mortar"],
        "description": "傳統零售持續衰退"
    },
    "fossil_fuel": {
        "penalty": -0.2,
        "keywords": ["coal", "oil refinery", "fossil"],
        "description": "化石燃料長期衰退"
    },
    "traditional_media": {
        "penalty": -0.2,
        "keywords": ["cable tv", "newspaper", "print media"],
        "description": "傳統媒體式微"
    }
}


class CycleBasedStrategySelector:
    """週期策略選擇器 - 多維度評分系統"""

    def __init__(self, macro_db: MacroDatabase = None, finance_db_path: str = "finance.db"):
        """
        初始化策略選擇器

        Args:
            macro_db: MacroDatabase 實例
            finance_db_path: 金融資料庫路徑
        """
        self.macro_db = macro_db or MacroDatabase()
        self.finance_db_path = finance_db_path
        self.cycle_analyzer = MarketCycleAnalyzer(db=self.macro_db)
        self.tech_analyzer = TechnicalAnalyzer(db_path=finance_db_path)
        self.strategies = CYCLE_STRATEGIES
        self.weights = SCORING_WEIGHTS

    def get_current_cycle(self) -> Dict:
        """取得當前市場週期"""
        return self.cycle_analyzer.get_current_cycle()

    def get_strategy_for_cycle(self, phase: str) -> Dict:
        """取得指定週期的策略配置"""
        strategy = self.strategies.get(phase, self.strategies["EXPANSION"])
        cycle_info = MARKET_CYCLES.get(phase, {})

        return {
            "phase": phase,
            "phase_name": cycle_info.get("name", phase),
            "phase_emoji": cycle_info.get("emoji", ""),
            "phase_color": cycle_info.get("color", "#888888"),
            **strategy
        }

    def get_current_strategy(self) -> Dict:
        """取得當前建議策略"""
        cycle = self.get_current_cycle()
        strategy = self.get_strategy_for_cycle(cycle["phase"])

        return {
            "cycle": cycle,
            "strategy": strategy,
            "allocation": {
                "stocks": strategy["stock_allocation"],
                "bonds": strategy["bond_allocation"],
                "cash": strategy["cash_allocation"]
            },
            "preferred_sectors": strategy["preferred_sectors"],
            "avoid_sectors": strategy["avoid_sectors"],
            "investment_style": strategy["investment_style"],
            "risk_tolerance": strategy["risk_tolerance"]
        }

    # ========== 多維度評分系統 ==========

    def calculate_cycle_fit_score(self, symbol: str, stock_info: Dict,
                                   preferred_sectors: List[str],
                                   avoid_sectors: List[str],
                                   phase: str) -> Tuple[float, List[str]]:
        """
        計算週期契合度分數

        評估：
        - 是否在當前週期偏好板塊
        - 是否避開不利板塊
        - 與週期投資風格的契合度

        Returns:
            (score 0-1, reasons list)
        """
        score = 0.5  # 基礎分
        reasons = []
        sector = stock_info.get("sector", "")

        # 板塊契合度
        if self._match_sector(sector, preferred_sectors):
            score += 0.4
            reasons.append(f"✓ 屬於週期偏好板塊 ({sector})")
        elif self._match_sector(sector, avoid_sectors):
            score -= 0.3
            reasons.append(f"✗ 屬於週期迴避板塊 ({sector})")
        else:
            reasons.append(f"○ 板塊中性 ({sector})")

        # 週期特定加分
        if phase == "EXPANSION":
            # 擴張期：偏好高成長、高 Beta
            pass  # 後續在其他分數中處理
        elif phase == "CONTRACTION":
            # 收縮期：偏好防禦性、低波動
            if sector and any(kw in sector.lower() for kw in ["utility", "staple", "healthcare"]):
                score += 0.1
                reasons.append("✓ 防禦性板塊，適合收縮期")

        return max(0, min(1, score)), reasons

    def calculate_moat_score(self, symbol: str, fundamentals: Dict) -> Tuple[float, List[str]]:
        """
        計算稀缺性/護城河分數

        評估：
        - 利潤率（定價能力）
        - 營業利潤率（營運效率）
        - ROE（資本效率）
        - 機構持股（專業認可度）

        Returns:
            (score 0-1, reasons list)
        """
        if not fundamentals:
            return 0.5, ["○ 無基本面數據，無法評估護城河"]

        scores = []
        reasons = []

        # 1. 利潤率 - 定價能力指標
        profit_margin = fundamentals.get("profit_margin")
        if profit_margin is not None:
            thresholds = MOAT_THRESHOLDS["profit_margin"]
            if profit_margin > thresholds["excellent"]:
                scores.append(1.0)
                reasons.append(f"✓ 高利潤率 {profit_margin:.1f}% - 強定價能力")
            elif profit_margin > thresholds["good"]:
                scores.append(0.7)
                reasons.append(f"○ 利潤率 {profit_margin:.1f}% - 中等")
            elif profit_margin > thresholds["poor"]:
                scores.append(0.4)
                reasons.append(f"△ 利潤率 {profit_margin:.1f}% - 偏低")
            else:
                scores.append(0.2)
                reasons.append(f"✗ 低利潤率 {profit_margin:.1f}% - 競爭激烈")

        # 2. 營業利潤率 - 營運效率
        operating_margin = fundamentals.get("operating_margin")
        if operating_margin is not None:
            thresholds = MOAT_THRESHOLDS["operating_margin"]
            if operating_margin > thresholds["excellent"]:
                scores.append(1.0)
                reasons.append(f"✓ 高營業利潤率 {operating_margin:.1f}% - 效率極佳")
            elif operating_margin > thresholds["good"]:
                scores.append(0.7)
            elif operating_margin > thresholds["poor"]:
                scores.append(0.4)
            else:
                scores.append(0.2)
                reasons.append(f"✗ 低營業利潤率 {operating_margin:.1f}%")

        # 3. ROE - 資本效率（巴菲特最愛指標）
        roe = fundamentals.get("roe")
        if roe is not None:
            thresholds = MOAT_THRESHOLDS["roe"]
            if roe > thresholds["excellent"]:
                scores.append(1.0)
                reasons.append(f"✓ 高 ROE {roe:.1f}% - 卓越資本效率")
            elif roe > thresholds["good"]:
                scores.append(0.7)
                reasons.append(f"○ ROE {roe:.1f}% - 良好")
            elif roe > thresholds["poor"]:
                scores.append(0.4)
            else:
                scores.append(0.2)
                reasons.append(f"✗ 低 ROE {roe:.1f}%")

        # 4. 機構持股 - 專業投資者認可
        inst_holding = fundamentals.get("held_by_institutions")
        if inst_holding is not None:
            inst_pct = inst_holding * 100 if inst_holding < 1 else inst_holding
            thresholds = MOAT_THRESHOLDS["held_by_institutions"]
            if inst_pct > thresholds["excellent"]:
                scores.append(0.9)
                reasons.append(f"✓ 高機構持股 {inst_pct:.0f}% - 專業認可")
            elif inst_pct > thresholds["good"]:
                scores.append(0.6)
            elif inst_pct > thresholds["poor"]:
                scores.append(0.4)

        # 綜合評分
        if scores:
            return sum(scores) / len(scores), reasons
        return 0.5, ["○ 護城河數據不足"]

    def calculate_growth_score(self, symbol: str, stock_info: Dict,
                                fundamentals: Dict) -> Tuple[float, List[str]]:
        """
        計算未來發展性分數 - 基於市場趨勢和產業前景

        評估：
        - 是否屬於市場熱門趨勢主題（AI、EV、綠能等）
        - 產業成長潛力
        - 是否屬於衰退中的產業

        Returns:
            (score 0-1, reasons list)
        """
        scores = []
        reasons = []

        # 取得股票的板塊和產業資訊
        sector = stock_info.get("sector", "").lower()
        industry = stock_info.get("industry", "").lower()
        name = stock_info.get("name", "").lower()

        # 合併所有可搜尋的文字
        searchable_text = f"{sector} {industry} {name} {symbol.lower()}"

        # 1. 檢查是否屬於熱門趨勢主題
        matched_trends = []
        for trend_name, trend_data in MARKET_MEGATRENDS.items():
            for keyword in trend_data["keywords"]:
                if keyword.lower() in searchable_text:
                    matched_trends.append({
                        "name": trend_name,
                        "score": trend_data["score"],
                        "description": trend_data["description"]
                    })
                    break  # 每個趨勢只匹配一次

        if matched_trends:
            # 取最高分的趨勢
            best_trend = max(matched_trends, key=lambda x: x["score"])
            scores.append(best_trend["score"])
            reasons.append(f"✓ 屬於熱門趨勢: {best_trend['description']}")

            # 如果匹配多個趨勢，額外加分
            if len(matched_trends) > 1:
                bonus = min(0.1 * (len(matched_trends) - 1), 0.2)
                scores.append(min(1.0, best_trend["score"] + bonus))
                other_trends = [t["name"] for t in matched_trends if t["name"] != best_trend["name"]]
                reasons.append(f"✓ 多重趨勢受惠: {', '.join(other_trends[:2])}")

        # 2. 檢查是否屬於衰退產業
        for decline_name, decline_data in DECLINING_TRENDS.items():
            for keyword in decline_data["keywords"]:
                if keyword.lower() in searchable_text:
                    scores.append(max(0, 0.5 + decline_data["penalty"]))
                    reasons.append(f"✗ {decline_data['description']}")
                    break

        # 3. 補充：財務面成長指標（權重較低）
        if fundamentals:
            # PEG Ratio
            peg = fundamentals.get("peg_ratio")
            if peg is not None and peg > 0:
                if peg < 1.0:
                    scores.append(0.8)
                    reasons.append(f"✓ PEG {peg:.2f} < 1 - 成長被市場低估")
                elif peg > 2.5:
                    scores.append(0.3)
                    reasons.append(f"△ PEG {peg:.2f} - 估值偏高")

            # Forward PE 折價
            pe = fundamentals.get("pe_ratio")
            forward_pe = fundamentals.get("forward_pe")
            if pe and forward_pe and pe > 0 and forward_pe > 0:
                discount = ((pe - forward_pe) / pe) * 100
                if discount > 15:
                    scores.append(0.8)
                    reasons.append(f"✓ 市場預期未來成長 (Forward PE 折價 {discount:.0f}%)")

        # 4. 如果沒有匹配任何趨勢，給予中性分數
        if not scores:
            scores.append(0.5)
            reasons.append("○ 無明顯市場趨勢標籤")

        return sum(scores) / len(scores), reasons

    def calculate_momentum_score(self, tech_analysis: Dict) -> Tuple[float, List[str]]:
        """
        計算動能分數（技術分析）

        Returns:
            (score 0-1, reasons list)
        """
        reasons = []

        combined_signal = tech_analysis.get("combined_signal", 0)
        recommendation = tech_analysis.get("recommendation", "HOLD")

        # 將 -1 到 1 的信號轉換為 0 到 1 的分數
        score = (combined_signal + 1) / 2

        if recommendation in ["STRONG_BUY"]:
            reasons.append(f"✓ 技術面強力買進信號 ({combined_signal:.2f})")
        elif recommendation in ["BUY"]:
            reasons.append(f"○ 技術面買進信號 ({combined_signal:.2f})")
        elif recommendation in ["STRONG_SELL"]:
            reasons.append(f"✗ 技術面強力賣出信號 ({combined_signal:.2f})")
        elif recommendation in ["SELL"]:
            reasons.append(f"△ 技術面賣出信號 ({combined_signal:.2f})")
        else:
            reasons.append(f"○ 技術面中性 ({combined_signal:.2f})")

        return max(0, min(1, score)), reasons

    def calculate_composite_score(self, symbol: str, stock_info: Dict,
                                   fundamentals: Dict, tech_analysis: Dict,
                                   preferred_sectors: List[str],
                                   avoid_sectors: List[str],
                                   phase: str) -> Dict:
        """
        計算綜合評分

        Returns:
            包含各維度分數和總分的字典
        """
        # 計算各維度分數
        cycle_score, cycle_reasons = self.calculate_cycle_fit_score(
            symbol, stock_info, preferred_sectors, avoid_sectors, phase
        )
        moat_score, moat_reasons = self.calculate_moat_score(symbol, fundamentals)
        growth_score, growth_reasons = self.calculate_growth_score(symbol, stock_info, fundamentals)
        momentum_score, momentum_reasons = self.calculate_momentum_score(tech_analysis)

        # 加權計算總分
        total_score = (
            cycle_score * self.weights["cycle_fit"] +
            moat_score * self.weights["moat"] +
            growth_score * self.weights["growth"] +
            momentum_score * self.weights["momentum"]
        )

        return {
            "symbol": symbol,
            "total_score": round(total_score, 3),
            "scores": {
                "cycle_fit": {"score": cycle_score, "weight": self.weights["cycle_fit"], "reasons": cycle_reasons},
                "moat": {"score": moat_score, "weight": self.weights["moat"], "reasons": moat_reasons},
                "growth": {"score": growth_score, "weight": self.weights["growth"], "reasons": growth_reasons},
                "momentum": {"score": momentum_score, "weight": self.weights["momentum"], "reasons": momentum_reasons},
            },
            "stock_info": stock_info,
            "fundamentals_summary": self._summarize_fundamentals(fundamentals),
            "tech_summary": {
                "recommendation": tech_analysis.get("recommendation", "N/A"),
                "signal": tech_analysis.get("combined_signal", 0)
            }
        }

    def _summarize_fundamentals(self, fundamentals: Dict) -> Dict:
        """整理基本面摘要"""
        if not fundamentals:
            return {}
        return {
            "pe_ratio": fundamentals.get("pe_ratio"),
            "forward_pe": fundamentals.get("forward_pe"),
            "peg_ratio": fundamentals.get("peg_ratio"),
            "profit_margin": fundamentals.get("profit_margin"),
            "roe": fundamentals.get("roe"),
            "debt_to_equity": fundamentals.get("debt_to_equity"),
        }

    # ========== 資料取得 ==========

    def _match_sector(self, stock_sector: str, target_sectors: List[str]) -> bool:
        """檢查股票板塊是否匹配目標板塊"""
        if not stock_sector:
            return False

        stock_sector_lower = stock_sector.lower()

        for target in target_sectors:
            if target.lower() in stock_sector_lower:
                return True
            keywords = SECTOR_MAPPING.get(target, [target])
            for keyword in keywords:
                if keyword.lower() in stock_sector_lower:
                    return True
        return False

    def _get_stock_info(self, symbol: str) -> Dict:
        """取得股票基本資訊"""
        try:
            conn = sqlite3.connect(self.finance_db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("""
                SELECT symbol, name, market, sector, industry
                FROM watchlist
                WHERE symbol = ?
            """, (symbol,))
            row = cursor.fetchone()
            conn.close()
            if row:
                return dict(row)
        except Exception as e:
            logger.error(f"取得 {symbol} 資訊失敗: {e}")
        return {"symbol": symbol, "sector": "", "industry": ""}

    def _get_fundamentals(self, symbol: str) -> Dict:
        """取得股票基本面數據"""
        try:
            conn = sqlite3.connect(self.finance_db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM fundamentals
                WHERE symbol = ?
                ORDER BY date DESC
                LIMIT 1
            """, (symbol,))
            row = cursor.fetchone()
            conn.close()
            if row:
                return dict(row)
        except Exception as e:
            logger.error(f"取得 {symbol} 基本面失敗: {e}")
        return {}

    def _get_all_symbols(self) -> List[str]:
        """取得所有追蹤的股票代碼"""
        try:
            conn = sqlite3.connect(self.finance_db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT symbol FROM watchlist WHERE is_active = 1")
            symbols = [row[0] for row in cursor.fetchall()]
            conn.close()
            return symbols
        except Exception as e:
            logger.error(f"取得股票清單失敗: {e}")
            return []

    # ========== 主要推薦方法 ==========

    def get_stock_recommendations(self, limit: int = 10) -> Dict:
        """
        取得基於多維度評分的股票推薦

        優先順序：
        1. 週期契合度 (30%)
        2. 稀缺性/護城河 (30%)
        3. 未來發展性 (25%)
        4. 動能 (15%)

        Returns:
            推薦結果
        """
        current_strategy = self.get_current_strategy()
        phase = current_strategy["cycle"]["phase"]
        preferred_sectors = current_strategy["preferred_sectors"]
        avoid_sectors = current_strategy["avoid_sectors"]

        # 取得所有股票並評分
        symbols = self._get_all_symbols()
        scored_stocks = []

        for symbol in symbols:
            try:
                # 取得各項數據
                stock_info = self._get_stock_info(symbol)
                fundamentals = self._get_fundamentals(symbol)
                tech_analysis = self.tech_analyzer.get_current_analysis(symbol)

                # 計算綜合評分
                result = self.calculate_composite_score(
                    symbol, stock_info, fundamentals, tech_analysis,
                    preferred_sectors, avoid_sectors, phase
                )

                # 過濾掉迴避板塊（但仍可作為賣出參考）
                sector = stock_info.get("sector", "")
                result["in_avoid_sector"] = self._match_sector(sector, avoid_sectors)
                result["in_preferred_sector"] = self._match_sector(sector, preferred_sectors)
                result["sector"] = sector

                scored_stocks.append(result)

            except Exception as e:
                logger.warning(f"分析 {symbol} 失敗: {e}")

        # 排序：總分由高到低
        scored_stocks.sort(key=lambda x: x["total_score"], reverse=True)

        # 分類推薦
        buy_recommendations = [
            s for s in scored_stocks
            if not s["in_avoid_sector"] and s["total_score"] >= 0.5
        ][:limit]

        # 賣出警示：低分或在迴避板塊
        sell_recommendations = [
            s for s in scored_stocks
            if s["total_score"] < 0.4 or s["in_avoid_sector"]
        ]
        sell_recommendations.sort(key=lambda x: x["total_score"])
        sell_recommendations = sell_recommendations[:limit]

        return {
            "phase": phase,
            "strategy": current_strategy["strategy"],
            "scoring_weights": self.weights,
            "buy_recommendations": buy_recommendations,
            "sell_recommendations": sell_recommendations,
            "preferred_sectors": preferred_sectors,
            "avoid_sectors": avoid_sectors,
            "allocation": current_strategy["allocation"],
            "total_analyzed": len(scored_stocks)
        }

    def get_stock_detail(self, symbol: str) -> Dict:
        """取得單一股票的詳細評分"""
        current_strategy = self.get_current_strategy()
        phase = current_strategy["cycle"]["phase"]
        preferred_sectors = current_strategy["preferred_sectors"]
        avoid_sectors = current_strategy["avoid_sectors"]

        stock_info = self._get_stock_info(symbol)
        fundamentals = self._get_fundamentals(symbol)
        tech_analysis = self.tech_analyzer.get_current_analysis(symbol)

        result = self.calculate_composite_score(
            symbol, stock_info, fundamentals, tech_analysis,
            preferred_sectors, avoid_sectors, phase
        )

        result["current_phase"] = phase
        result["strategy"] = current_strategy["strategy"]["name"]

        return result

    def get_allocation_chart_data(self) -> Dict:
        """取得資產配置圖表數據"""
        strategy = self.get_current_strategy()
        allocation = strategy["allocation"]

        return {
            "labels": ["股票", "債券", "現金"],
            "values": [
                allocation["stocks"] * 100,
                allocation["bonds"] * 100,
                allocation["cash"] * 100
            ],
            "colors": ["#4CAF50", "#2196F3", "#FFC107"]
        }

    def get_strategy_summary(self) -> str:
        """取得策略摘要文字"""
        result = self.get_current_strategy()
        cycle = result["cycle"]
        strategy = result["strategy"]

        summary = f"""
週期策略建議報告
================
日期: {cycle['date']}

當前週期: {strategy['phase_emoji']} {strategy['phase_name']}
週期分數: {cycle['score']:.2f}
判斷信心: {cycle['confidence']:.0%}

建議策略: {strategy['name']}
風險容忍: {strategy['risk_tolerance']}

資產配置建議:
  股票: {strategy['stock_allocation']:.0%}
  債券: {strategy['bond_allocation']:.0%}
  現金: {strategy['cash_allocation']:.0%}

偏好板塊: {', '.join(strategy['preferred_sectors'])}
迴避板塊: {', '.join(strategy['avoid_sectors'])}
投資風格: {', '.join(strategy['investment_style'])}

評分權重:
  週期契合度: {self.weights['cycle_fit']:.0%}
  稀缺性/護城河: {self.weights['moat']:.0%}
  未來發展性: {self.weights['growth']:.0%}
  動能: {self.weights['momentum']:.0%}
"""
        return summary


def main():
    """主程式 - 用於測試"""
    print("週期策略選擇器 - 多維度評分系統")
    print("=" * 60)

    selector = CycleBasedStrategySelector()

    # 顯示策略摘要
    summary = selector.get_strategy_summary()
    print(summary)

    # 顯示股票推薦
    print("\n" + "=" * 60)
    print("股票推薦 (依總分排序)")
    print("=" * 60)

    recommendations = selector.get_stock_recommendations(limit=5)

    print(f"\n📈 買進推薦 (當前週期: {recommendations['phase']})")
    print("-" * 60)

    for i, rec in enumerate(recommendations["buy_recommendations"], 1):
        print(f"\n{i}. {rec['symbol']} - 總分: {rec['total_score']:.2f}")
        print(f"   板塊: {rec['sector']}" +
              (" ⭐偏好板塊" if rec['in_preferred_sector'] else ""))

        # 顯示各維度分數
        for dim, data in rec['scores'].items():
            print(f"   {dim}: {data['score']:.2f} (權重 {data['weight']:.0%})")
            for reason in data['reasons'][:2]:  # 只顯示前2個理由
                print(f"      {reason}")

    print(f"\n📉 賣出警示")
    print("-" * 60)
    for rec in recommendations["sell_recommendations"][:3]:
        print(f"  {rec['symbol']}: 總分 {rec['total_score']:.2f}")


if __name__ == "__main__":
    main()
