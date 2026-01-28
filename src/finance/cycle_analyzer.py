"""
市場週期分析器

分析總經數據判斷當前市場週期階段:
- EXPANSION (擴張期): 經濟成長、就業增加
- PEAK (高峰期): 經濟過熱、通膨上升
- CONTRACTION (收縮期): 經濟放緩、失業上升
- TROUGH (谷底期): 經濟觸底、復甦跡象
"""

import logging
from datetime import datetime, date, timedelta
from typing import Dict, Optional, Tuple, List
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from config.macro_indicators import (
    DIMENSION_WEIGHTS,
    MARKET_CYCLES,
    INDICATOR_THRESHOLDS,
)
from src.finance.macro_database import MacroDatabase


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class MarketCycleAnalyzer:
    """市場週期分析器"""

    def __init__(self, db: MacroDatabase = None):
        """
        初始化分析器

        Args:
            db: MacroDatabase 實例
        """
        self.db = db or MacroDatabase()
        self.weights = DIMENSION_WEIGHTS
        self.thresholds = INDICATOR_THRESHOLDS

    def analyze_yield_curve(self) -> Dict:
        """
        分析殖利率曲線

        Returns:
            分析結果，包含 score (-1 to 1), signal, details
        """
        # 取得 10Y-2Y 利差
        t10y2y_data = self.db.get_macro_data("T10Y2Y", limit=30)
        # 取得 10Y-3M 利差
        t10y3m_data = self.db.get_macro_data("T10Y3M", limit=30)

        if not t10y2y_data:
            return {"score": 0, "signal": "NO_DATA", "details": "無殖利率曲線數據"}

        current_spread = t10y2y_data[0]["value"]
        current_10y3m = t10y3m_data[0]["value"] if t10y3m_data else None

        # 計算趨勢 (最近 vs 30天前)
        if len(t10y2y_data) >= 30:
            trend = current_spread - t10y2y_data[-1]["value"]
        else:
            trend = 0

        # 判斷信號
        if current_spread < self.thresholds["yield_curve_inversion"]:
            # 殖利率曲線倒掛 - 強烈衰退信號
            if current_10y3m is not None and current_10y3m < 0:
                score = -0.9  # 兩者都倒掛，非常強烈的衰退信號
                signal = "DEEPLY_INVERTED"
            else:
                score = -0.7
                signal = "INVERTED"
        elif current_spread < 0.5:
            # 接近倒掛
            score = -0.3
            signal = "FLATTENING"
        elif current_spread < self.thresholds["yield_curve_steep"]:
            # 正常
            score = 0.3
            signal = "NORMAL"
        else:
            # 陡峭 - 經濟擴張信號
            score = 0.7
            signal = "STEEP"

        # 根據趨勢微調
        if trend > 0.3:
            score = min(1, score + 0.2)
        elif trend < -0.3:
            score = max(-1, score - 0.2)

        return {
            "score": score,
            "signal": signal,
            "details": {
                "10y2y_spread": current_spread,
                "10y3m_spread": current_10y3m,
                "trend_30d": trend,
                "is_inverted": current_spread < 0
            }
        }

    def analyze_employment(self) -> Dict:
        """
        分析就業市場

        Returns:
            分析結果
        """
        # 取得失業率
        unrate_data = self.db.get_macro_data("UNRATE", limit=12)
        # 取得初領失業金
        icsa_data = self.db.get_macro_data("ICSA", limit=12)
        # 取得非農就業
        payems_data = self.db.get_macro_data("PAYEMS", limit=12)

        if not unrate_data:
            return {"score": 0, "signal": "NO_DATA", "details": "無就業數據"}

        current_unrate = unrate_data[0]["value"]
        unrate_change = unrate_data[0].get("change_value", 0) or 0

        # 計算 6 個月趨勢
        if len(unrate_data) >= 6:
            unrate_trend = current_unrate - unrate_data[5]["value"]
        else:
            unrate_trend = 0

        # 初領失業金趨勢
        icsa_trend = 0
        current_icsa = None
        if icsa_data and len(icsa_data) >= 4:
            current_icsa = icsa_data[0]["value"]
            icsa_trend = current_icsa - icsa_data[3]["value"]

        # 非農就業趨勢
        payems_growth = 0
        if payems_data and len(payems_data) >= 2:
            payems_growth = payems_data[0].get("change_value", 0) or 0

        # 判斷信號
        if current_unrate < self.thresholds["unemployment_low"]:
            if unrate_trend < 0:
                score = 0.8  # 低失業率且持續下降
                signal = "VERY_STRONG"
            else:
                score = 0.5  # 低失業率
                signal = "STRONG"
        elif current_unrate < self.thresholds["unemployment_high"]:
            if unrate_trend > 0.5:
                score = -0.3  # 中等但上升
                signal = "WEAKENING"
            elif unrate_trend < -0.3:
                score = 0.3  # 中等但下降
                signal = "IMPROVING"
            else:
                score = 0  # 穩定
                signal = "STABLE"
        elif current_unrate < self.thresholds["unemployment_crisis"]:
            score = -0.6  # 高失業率
            signal = "WEAK"
        else:
            score = -0.9  # 危機水準
            signal = "CRISIS"

        # 根據初領失業金微調
        if icsa_trend and abs(icsa_trend) > 20000:
            if icsa_trend > 0:
                score = max(-1, score - 0.15)
            else:
                score = min(1, score + 0.1)

        return {
            "score": score,
            "signal": signal,
            "details": {
                "unemployment_rate": current_unrate,
                "unemployment_trend_6m": unrate_trend,
                "initial_claims": current_icsa,
                "initial_claims_trend": icsa_trend,
                "nonfarm_payroll_change": payems_growth
            }
        }

    def analyze_growth(self) -> Dict:
        """
        分析經濟成長

        Returns:
            分析結果
        """
        # 取得 GDP
        gdp_data = self.db.get_macro_data("GDP", limit=8)
        # 取得工業生產
        indpro_data = self.db.get_macro_data("INDPRO", limit=12)

        if not gdp_data:
            return {"score": 0, "signal": "NO_DATA", "details": "無 GDP 數據"}

        current_gdp = gdp_data[0]["value"]
        gdp_change_pct = gdp_data[0].get("change_pct", 0) or 0

        # 計算 GDP 年增率 (YoY)
        gdp_yoy = 0
        if len(gdp_data) >= 4:
            prev_year_gdp = gdp_data[3]["value"]
            if prev_year_gdp:
                gdp_yoy = ((current_gdp - prev_year_gdp) / prev_year_gdp) * 100

        # 工業生產趨勢
        indpro_change = 0
        if indpro_data and len(indpro_data) >= 2:
            indpro_change = indpro_data[0].get("change_pct", 0) or 0

        # 判斷信號
        if gdp_change_pct > self.thresholds["gdp_strong"]:
            score = 0.8
            signal = "STRONG_GROWTH"
        elif gdp_change_pct > self.thresholds["gdp_weak"]:
            score = 0.4
            signal = "MODERATE_GROWTH"
        elif gdp_change_pct > self.thresholds["gdp_recession"]:
            score = 0
            signal = "SLOW_GROWTH"
        elif gdp_change_pct > -2:
            score = -0.5
            signal = "CONTRACTION"
        else:
            score = -0.9
            signal = "DEEP_RECESSION"

        # 根據工業生產微調
        if indpro_change:
            if indpro_change > 0.5:
                score = min(1, score + 0.15)
            elif indpro_change < -0.5:
                score = max(-1, score - 0.15)

        return {
            "score": score,
            "signal": signal,
            "details": {
                "gdp": current_gdp,
                "gdp_qoq_pct": gdp_change_pct,
                "gdp_yoy_pct": gdp_yoy,
                "industrial_production_change": indpro_change
            }
        }

    def analyze_inflation(self) -> Dict:
        """
        分析通貨膨脹

        Returns:
            分析結果
        """
        # 取得 CPI
        cpi_data = self.db.get_macro_data("CPIAUCSL", limit=13)
        # 取得聯邦基金利率
        fedfunds_data = self.db.get_macro_data("FEDFUNDS", limit=12)

        if not cpi_data:
            return {"score": 0, "signal": "NO_DATA", "details": "無通膨數據"}

        current_cpi = cpi_data[0]["value"]
        cpi_mom = cpi_data[0].get("change_pct", 0) or 0

        # 計算年通膨率 (YoY)
        cpi_yoy = 0
        if len(cpi_data) >= 12:
            prev_year_cpi = cpi_data[11]["value"]
            if prev_year_cpi:
                cpi_yoy = ((current_cpi - prev_year_cpi) / prev_year_cpi) * 100

        # 聯邦基金利率
        current_fedfunds = fedfunds_data[0]["value"] if fedfunds_data else None
        fedfunds_change = 0
        if fedfunds_data and len(fedfunds_data) >= 3:
            fedfunds_change = current_fedfunds - fedfunds_data[2]["value"]

        # 判斷信號 (通膨過高或過低都不好)
        if cpi_yoy < 1:
            score = -0.3  # 通縮風險
            signal = "DEFLATION_RISK"
        elif cpi_yoy < 2:
            score = 0.5  # 理想通膨
            signal = "LOW_STABLE"
        elif cpi_yoy < 3:
            score = 0.3  # 略高但可接受
            signal = "MODERATE"
        elif cpi_yoy < 5:
            score = -0.2  # 偏高，Fed 可能升息
            signal = "ELEVATED"
        elif cpi_yoy < 7:
            score = -0.5  # 高通膨
            signal = "HIGH"
        else:
            score = -0.8  # 嚴重通膨
            signal = "VERY_HIGH"

        # 根據 Fed 動作微調
        if fedfunds_change:
            if fedfunds_change > 0.25:
                # Fed 升息 - 抑制過熱
                score = max(-1, score - 0.1)
            elif fedfunds_change < -0.25:
                # Fed 降息 - 刺激經濟
                score = min(1, score + 0.1)

        return {
            "score": score,
            "signal": signal,
            "details": {
                "cpi": current_cpi,
                "cpi_mom_pct": cpi_mom,
                "cpi_yoy_pct": cpi_yoy,
                "fed_funds_rate": current_fedfunds,
                "fed_funds_change_3m": fedfunds_change
            }
        }

    def analyze_sentiment(self) -> Dict:
        """
        分析市場情緒

        Returns:
            分析結果
        """
        # 取得消費者信心
        umcsent_data = self.db.get_macro_data("UMCSENT", limit=6)
        # 取得 VIX
        vix_data = self.db.get_macro_data("VIXCLS", limit=30)

        score = 0
        details = {}

        # 分析消費者信心
        if umcsent_data:
            current_sentiment = umcsent_data[0]["value"]
            sentiment_trend = 0
            if len(umcsent_data) >= 3:
                sentiment_trend = current_sentiment - umcsent_data[2]["value"]

            if current_sentiment > self.thresholds["sentiment_high"]:
                sentiment_score = 0.6
                sentiment_signal = "HIGH"
            elif current_sentiment > self.thresholds["sentiment_low"]:
                sentiment_score = 0.2
                sentiment_signal = "MODERATE"
            else:
                sentiment_score = -0.4
                sentiment_signal = "LOW"

            # 趨勢調整
            if sentiment_trend > 5:
                sentiment_score = min(1, sentiment_score + 0.2)
            elif sentiment_trend < -5:
                sentiment_score = max(-1, sentiment_score - 0.2)

            details["consumer_sentiment"] = current_sentiment
            details["sentiment_trend_3m"] = sentiment_trend
            details["sentiment_signal"] = sentiment_signal
            score += sentiment_score * 0.5

        # 分析 VIX
        if vix_data:
            current_vix = vix_data[0]["value"]
            vix_avg = sum(d["value"] for d in vix_data) / len(vix_data)

            if current_vix < self.thresholds["vix_low"]:
                vix_score = 0.5  # 低波動，市場平靜
                vix_signal = "LOW"
            elif current_vix < self.thresholds["vix_elevated"]:
                vix_score = 0.2
                vix_signal = "NORMAL"
            elif current_vix < self.thresholds["vix_high"]:
                vix_score = -0.3
                vix_signal = "ELEVATED"
            else:
                vix_score = -0.7  # 高恐慌
                vix_signal = "HIGH_FEAR"

            details["vix"] = current_vix
            details["vix_30d_avg"] = vix_avg
            details["vix_signal"] = vix_signal
            score += vix_score * 0.5

        if not umcsent_data and not vix_data:
            return {"score": 0, "signal": "NO_DATA", "details": "無情緒數據"}

        # 綜合信號
        if score > 0.3:
            signal = "OPTIMISTIC"
        elif score > 0:
            signal = "NEUTRAL_POSITIVE"
        elif score > -0.3:
            signal = "NEUTRAL_NEGATIVE"
        else:
            signal = "PESSIMISTIC"

        return {
            "score": score,
            "signal": signal,
            "details": details
        }

    def calculate_composite_score(self, signals: Dict) -> float:
        """
        計算綜合分數

        Args:
            signals: 各維度分析結果

        Returns:
            綜合分數 (-1 to 1)
        """
        score = 0
        for dimension, weight in self.weights.items():
            if dimension in signals:
                dim_score = signals[dimension].get("score", 0)
                score += dim_score * weight

        return round(score, 3)

    def determine_phase(self, score: float, signals: Dict) -> str:
        """
        判斷市場週期階段

        Args:
            score: 綜合分數
            signals: 各維度分析結果

        Returns:
            週期階段 (EXPANSION/PEAK/CONTRACTION/TROUGH)
        """
        yield_curve = signals.get("yield_curve", {})
        employment = signals.get("employment", {})
        growth = signals.get("growth", {})
        sentiment = signals.get("sentiment", {})

        yield_inverted = yield_curve.get("score", 0) < 0
        employment_weak = employment.get("score", 0) < -0.3
        growth_negative = growth.get("score", 0) < -0.5
        sentiment_improving = sentiment.get("score", 0) > 0

        # 判斷邏輯
        if score > 0.3:
            # 整體正面
            if yield_inverted:
                return "PEAK"  # 殖利率倒掛是警訊
            else:
                return "EXPANSION"
        elif score > 0:
            # 略為正面
            if yield_inverted:
                return "PEAK"
            else:
                return "EXPANSION"
        elif score > -0.3:
            # 略為負面
            if growth_negative:
                return "CONTRACTION"
            else:
                return "PEAK"
        else:
            # 明顯負面
            if sentiment_improving and not employment_weak:
                return "TROUGH"  # 情緒好轉，可能觸底
            else:
                return "CONTRACTION"

    def calculate_confidence(self, signals: Dict) -> float:
        """
        計算判斷信心度

        Args:
            signals: 各維度分析結果

        Returns:
            信心度 (0 to 1)
        """
        # 檢查有多少維度有數據
        valid_dimensions = 0
        total_weight = 0

        for dimension, weight in self.weights.items():
            if dimension in signals:
                if signals[dimension].get("signal") != "NO_DATA":
                    valid_dimensions += 1
                    total_weight += weight

        # 基礎信心度取決於數據完整性
        data_confidence = total_weight

        # 檢查信號一致性
        scores = [s.get("score", 0) for s in signals.values()
                  if s.get("signal") != "NO_DATA"]
        if scores:
            # 如果所有信號同向，信心度高
            all_positive = all(s > 0 for s in scores)
            all_negative = all(s < 0 for s in scores)
            if all_positive or all_negative:
                consistency_bonus = 0.2
            else:
                consistency_bonus = 0

            confidence = min(1, data_confidence + consistency_bonus)
        else:
            confidence = 0

        return round(confidence, 2)

    def get_current_cycle(self) -> Dict:
        """
        取得當前市場週期分析

        Returns:
            完整分析結果
        """
        # 執行各維度分析
        signals = {
            "yield_curve": self.analyze_yield_curve(),
            "employment": self.analyze_employment(),
            "growth": self.analyze_growth(),
            "inflation": self.analyze_inflation(),
            "sentiment": self.analyze_sentiment(),
        }

        # 計算綜合分數
        score = self.calculate_composite_score(signals)

        # 判斷週期階段
        phase = self.determine_phase(score, signals)

        # 計算信心度
        confidence = self.calculate_confidence(signals)

        # 取得週期資訊
        cycle_info = MARKET_CYCLES.get(phase, {})

        result = {
            "date": date.today(),
            "phase": phase,
            "phase_name": cycle_info.get("name", phase),
            "phase_emoji": cycle_info.get("emoji", ""),
            "phase_color": cycle_info.get("color", "#888888"),
            "phase_description": cycle_info.get("description", ""),
            "score": score,
            "confidence": confidence,
            "signals": signals,
            "weights": self.weights
        }

        # 存入資料庫
        self.db.insert_market_cycle(
            date=result["date"],
            phase=phase,
            score=score,
            confidence=confidence,
            signals=signals,
            recommended_strategy=None  # 由 CycleStrategySelector 填入
        )

        return result

    def get_cycle_summary(self) -> str:
        """取得週期摘要文字"""
        cycle = self.get_current_cycle()

        summary = f"""
市場週期分析報告
================
日期: {cycle['date']}

當前週期: {cycle['phase_emoji']} {cycle['phase_name']} ({cycle['phase']})
綜合分數: {cycle['score']:.2f} (範圍: -1 到 1)
判斷信心: {cycle['confidence']:.0%}

說明: {cycle['phase_description']}

各維度分析:
"""
        for dim, data in cycle['signals'].items():
            weight = self.weights.get(dim, 0)
            summary += f"  - {dim}: {data['signal']} (分數: {data['score']:.2f}, 權重: {weight:.0%})\n"

        return summary


def main():
    """主程式 - 用於測試"""
    print("市場週期分析器")
    print("=" * 50)

    analyzer = MarketCycleAnalyzer()
    summary = analyzer.get_cycle_summary()
    print(summary)


if __name__ == "__main__":
    main()
