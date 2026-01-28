"""
週期策略回測系統

使用歷史總經數據回測週期策略的績效表現。
"""

import logging
import sqlite3
from datetime import datetime, date, timedelta
from typing import Dict, List, Optional, Tuple
from pathlib import Path
import pandas as pd
import numpy as np

import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from config.macro_indicators import CYCLE_STRATEGIES, MARKET_CYCLES, DIMENSION_WEIGHTS
from src.finance.macro_database import MacroDatabase


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class CycleBacktester:
    """週期策略回測器"""

    def __init__(self, macro_db: MacroDatabase = None, finance_db_path: str = "finance.db"):
        """
        初始化回測器

        Args:
            macro_db: MacroDatabase 實例
            finance_db_path: 金融資料庫路徑
        """
        self.macro_db = macro_db or MacroDatabase()
        self.finance_db_path = finance_db_path
        self.strategies = CYCLE_STRATEGIES

    def get_historical_cycles(self, start_date: date, end_date: date) -> List[Dict]:
        """
        根據歷史總經數據計算各日期的市場週期

        Returns:
            每月的週期判斷列表
        """
        cycles = []

        # 取得所有總經數據
        conn = sqlite3.connect(self.macro_db.db_path)
        conn.row_factory = sqlite3.Row

        # 按月計算週期
        current = start_date.replace(day=1)
        while current <= end_date:
            cycle = self._calculate_cycle_for_date(conn, current)
            if cycle:
                cycles.append(cycle)

            # 移到下個月
            if current.month == 12:
                current = current.replace(year=current.year + 1, month=1)
            else:
                current = current.replace(month=current.month + 1)

        conn.close()
        return cycles

    def _calculate_cycle_for_date(self, conn, target_date: date) -> Optional[Dict]:
        """計算特定日期的市場週期"""
        cursor = conn.cursor()

        signals = {}
        scores = []

        # 1. 殖利率曲線
        cursor.execute("""
            SELECT value FROM macro_data
            WHERE series_id = 'T10Y2Y' AND date <= ?
            ORDER BY date DESC LIMIT 1
        """, (target_date,))
        row = cursor.fetchone()
        if row:
            spread = row[0]
            if spread < 0:
                yield_score = -0.7 if spread < -0.5 else -0.4
            elif spread < 0.5:
                yield_score = -0.2
            elif spread < 1.5:
                yield_score = 0.3
            else:
                yield_score = 0.6
            signals["yield_curve"] = {"value": spread, "score": yield_score}
            scores.append(yield_score * DIMENSION_WEIGHTS.get("yield_curve", 0.25))

        # 2. 失業率
        cursor.execute("""
            SELECT value FROM macro_data
            WHERE series_id = 'UNRATE' AND date <= ?
            ORDER BY date DESC LIMIT 1
        """, (target_date,))
        row = cursor.fetchone()
        if row:
            unrate = row[0]
            if unrate < 4:
                emp_score = 0.7
            elif unrate < 5:
                emp_score = 0.4
            elif unrate < 6:
                emp_score = 0
            elif unrate < 8:
                emp_score = -0.4
            else:
                emp_score = -0.8
            signals["employment"] = {"value": unrate, "score": emp_score}
            scores.append(emp_score * DIMENSION_WEIGHTS.get("employment", 0.20))

        # 3. VIX
        cursor.execute("""
            SELECT value FROM macro_data
            WHERE series_id = 'VIXCLS' AND date <= ?
            ORDER BY date DESC LIMIT 1
        """, (target_date,))
        row = cursor.fetchone()
        if row:
            vix = row[0]
            if vix < 15:
                sent_score = 0.5
            elif vix < 20:
                sent_score = 0.2
            elif vix < 30:
                sent_score = -0.3
            else:
                sent_score = -0.7
            signals["sentiment"] = {"value": vix, "score": sent_score}
            scores.append(sent_score * DIMENSION_WEIGHTS.get("sentiment", 0.15))

        # 4. 聯邦基金利率趨勢
        cursor.execute("""
            SELECT value FROM macro_data
            WHERE series_id = 'FEDFUNDS' AND date <= ?
            ORDER BY date DESC LIMIT 3
        """, (target_date,))
        rows = cursor.fetchall()
        if len(rows) >= 2:
            rate_change = rows[0][0] - rows[-1][0]
            if rate_change > 0.25:
                inf_score = -0.3  # 升息
            elif rate_change < -0.25:
                inf_score = 0.3   # 降息
            else:
                inf_score = 0
            signals["inflation"] = {"value": rows[0][0], "change": rate_change, "score": inf_score}
            scores.append(inf_score * DIMENSION_WEIGHTS.get("inflation", 0.15))

        if not scores:
            return None

        # 計算總分
        total_score = sum(scores)

        # 判斷週期
        yield_inverted = signals.get("yield_curve", {}).get("value", 1) < 0

        if total_score > 0.2:
            phase = "PEAK" if yield_inverted else "EXPANSION"
        elif total_score > -0.1:
            phase = "PEAK" if yield_inverted else "EXPANSION"
        elif total_score > -0.3:
            phase = "CONTRACTION"
        else:
            phase = "TROUGH" if signals.get("sentiment", {}).get("score", 0) > -0.3 else "CONTRACTION"

        return {
            "date": target_date,
            "phase": phase,
            "score": total_score,
            "signals": signals
        }

    def get_stock_prices(self, symbol: str, start_date: date, end_date: date) -> pd.DataFrame:
        """取得股票歷史價格"""
        conn = sqlite3.connect(self.finance_db_path)
        query = """
            SELECT date, open, high, low, close, volume
            FROM daily_prices
            WHERE symbol = ? AND date >= ? AND date <= ?
            ORDER BY date
        """
        df = pd.read_sql_query(query, conn, params=(symbol, start_date, end_date))
        conn.close()

        if not df.empty:
            df['date'] = pd.to_datetime(df['date'])
        return df

    def get_sector_for_symbol(self, symbol: str) -> str:
        """取得股票板塊"""
        conn = sqlite3.connect(self.finance_db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT sector FROM watchlist WHERE symbol = ?", (symbol,))
        row = cursor.fetchone()
        conn.close()
        return row[0] if row else ""

    def backtest_strategy(self, start_date: date, end_date: date,
                          initial_capital: float = 100000,
                          rebalance_frequency: str = "monthly") -> Dict:
        """
        執行策略回測

        Args:
            start_date: 開始日期
            end_date: 結束日期
            initial_capital: 初始資金
            rebalance_frequency: 再平衡頻率 (monthly/quarterly)

        Returns:
            回測結果
        """
        # 1. 取得歷史週期
        cycles = self.get_historical_cycles(start_date, end_date)
        if not cycles:
            return {"error": "無法取得歷史週期數據"}

        logger.info(f"回測期間: {start_date} ~ {end_date}")
        logger.info(f"共 {len(cycles)} 個月的週期數據")

        # 2. 取得所有可交易股票
        conn = sqlite3.connect(self.finance_db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT symbol, sector FROM watchlist WHERE is_active = 1")
        stocks = {row[0]: row[1] for row in cursor.fetchall()}
        conn.close()

        # 3. 模擬交易
        portfolio_value = initial_capital
        cash = initial_capital
        holdings = {}  # {symbol: shares}
        trades = []
        equity_curve = []
        cycle_changes = []

        prev_phase = None

        for cycle in cycles:
            cycle_date = cycle["date"]
            phase = cycle["phase"]
            strategy = self.strategies.get(phase, self.strategies["EXPANSION"])

            # 記錄週期變化
            if phase != prev_phase:
                cycle_changes.append({
                    "date": cycle_date,
                    "from_phase": prev_phase,
                    "to_phase": phase,
                    "score": cycle["score"]
                })
                prev_phase = phase

            # 決定配置
            stock_allocation = strategy["stock_allocation"]
            preferred_sectors = strategy["preferred_sectors"]
            avoid_sectors = strategy["avoid_sectors"]

            # 計算目標持股
            target_stock_value = portfolio_value * stock_allocation

            # 選擇符合板塊的股票
            eligible_stocks = []
            for symbol, sector in stocks.items():
                sector_lower = (sector or "").lower()

                # 檢查是否在迴避板塊
                in_avoid = any(av.lower() in sector_lower for av in avoid_sectors)
                if in_avoid:
                    continue

                # 檢查是否在偏好板塊
                in_preferred = any(pf.lower() in sector_lower for pf in preferred_sectors)

                eligible_stocks.append({
                    "symbol": symbol,
                    "sector": sector,
                    "preferred": in_preferred
                })

            # 優先選擇偏好板塊的股票
            eligible_stocks.sort(key=lambda x: (not x["preferred"], x["symbol"]))

            # 取前 N 支股票平均分配
            max_stocks = 10
            selected_stocks = eligible_stocks[:max_stocks]

            if not selected_stocks:
                continue

            # 取得這些股票在該月的價格
            month_end = (cycle_date.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)

            # 賣出不在列表中的持股
            for symbol in list(holdings.keys()):
                if symbol not in [s["symbol"] for s in selected_stocks]:
                    # 賣出
                    prices = self.get_stock_prices(symbol, cycle_date, month_end)
                    if not prices.empty:
                        sell_price = prices.iloc[-1]["close"]
                        proceeds = holdings[symbol] * sell_price
                        cash += proceeds
                        trades.append({
                            "date": cycle_date,
                            "symbol": symbol,
                            "action": "SELL",
                            "shares": holdings[symbol],
                            "price": sell_price,
                            "value": proceeds,
                            "reason": f"週期 {phase} 不符合板塊偏好"
                        })
                        del holdings[symbol]

            # 買入新股票
            per_stock_value = target_stock_value / len(selected_stocks) if selected_stocks else 0

            for stock in selected_stocks:
                symbol = stock["symbol"]
                prices = self.get_stock_prices(symbol, cycle_date, month_end)

                if prices.empty:
                    continue

                buy_price = prices.iloc[0]["close"]
                target_shares = int(per_stock_value / buy_price)

                current_shares = holdings.get(symbol, 0)

                if target_shares > current_shares:
                    # 買入
                    shares_to_buy = target_shares - current_shares
                    cost = shares_to_buy * buy_price

                    if cost <= cash:
                        holdings[symbol] = target_shares
                        cash -= cost
                        trades.append({
                            "date": cycle_date,
                            "symbol": symbol,
                            "action": "BUY",
                            "shares": shares_to_buy,
                            "price": buy_price,
                            "value": cost,
                            "reason": f"週期 {phase} 偏好板塊" if stock["preferred"] else f"週期 {phase} 配置"
                        })

            # 計算月底組合價值
            month_value = cash
            for symbol, shares in holdings.items():
                prices = self.get_stock_prices(symbol, cycle_date, month_end)
                if not prices.empty:
                    month_value += shares * prices.iloc[-1]["close"]

            portfolio_value = month_value
            equity_curve.append({
                "date": cycle_date,
                "value": portfolio_value,
                "phase": phase,
                "cash": cash,
                "holdings_count": len(holdings)
            })

        # 4. 計算績效指標
        if not equity_curve:
            return {"error": "回測無交易記錄"}

        equity_df = pd.DataFrame(equity_curve)
        equity_df["return"] = equity_df["value"].pct_change()

        total_return = (portfolio_value - initial_capital) / initial_capital * 100
        annualized_return = ((portfolio_value / initial_capital) ** (12 / len(equity_curve)) - 1) * 100

        # 最大回撤
        equity_df["cummax"] = equity_df["value"].cummax()
        equity_df["drawdown"] = (equity_df["value"] - equity_df["cummax"]) / equity_df["cummax"]
        max_drawdown = equity_df["drawdown"].min() * 100

        # 夏普比率
        returns = equity_df["return"].dropna()
        if len(returns) > 1 and returns.std() > 0:
            sharpe_ratio = (returns.mean() / returns.std()) * np.sqrt(12)
        else:
            sharpe_ratio = 0

        # 勝率
        winning_months = (returns > 0).sum()
        total_months = len(returns)
        win_rate = winning_months / total_months * 100 if total_months > 0 else 0

        # 各週期績效
        phase_performance = {}
        for phase in ["EXPANSION", "PEAK", "CONTRACTION", "TROUGH"]:
            phase_equity = equity_df[equity_df["phase"] == phase]
            if not phase_equity.empty:
                phase_returns = phase_equity["return"].dropna()
                phase_performance[phase] = {
                    "months": len(phase_equity),
                    "avg_return": phase_returns.mean() * 100 if len(phase_returns) > 0 else 0,
                    "total_return": ((1 + phase_returns).prod() - 1) * 100 if len(phase_returns) > 0 else 0
                }

        return {
            "period": {
                "start": start_date.isoformat(),
                "end": end_date.isoformat(),
                "months": len(equity_curve)
            },
            "performance": {
                "initial_capital": initial_capital,
                "final_value": portfolio_value,
                "total_return_pct": total_return,
                "annualized_return_pct": annualized_return,
                "max_drawdown_pct": max_drawdown,
                "sharpe_ratio": sharpe_ratio,
                "win_rate_pct": win_rate,
                "total_trades": len(trades)
            },
            "phase_performance": phase_performance,
            "cycle_changes": cycle_changes,
            "equity_curve": equity_curve,
            "trades": trades[-20:],  # 最近 20 筆交易
            "final_holdings": holdings
        }

    def compare_with_benchmark(self, start_date: date, end_date: date,
                                benchmark_symbol: str = "^GSPC") -> Dict:
        """與基準指數比較"""
        # 取得基準指數價格
        benchmark_prices = self.get_stock_prices(benchmark_symbol, start_date, end_date)

        if benchmark_prices.empty:
            # 嘗試 SPY
            benchmark_prices = self.get_stock_prices("SPY", start_date, end_date)

        if benchmark_prices.empty:
            return {"error": "無法取得基準指數數據"}

        # 計算買入持有報酬
        start_price = benchmark_prices.iloc[0]["close"]
        end_price = benchmark_prices.iloc[-1]["close"]
        benchmark_return = (end_price - start_price) / start_price * 100

        return {
            "symbol": benchmark_symbol,
            "start_price": start_price,
            "end_price": end_price,
            "total_return_pct": benchmark_return
        }


def main():
    """主程式 - 執行回測"""
    print("=" * 60)
    print("週期策略回測系統")
    print("=" * 60)

    backtester = CycleBacktester()

    # 設定回測期間 (2025 年)
    start_date = date(2025, 1, 1)
    end_date = date(2025, 12, 31)

    print(f"\n回測期間: {start_date} ~ {end_date}")
    print("-" * 60)

    # 執行回測
    result = backtester.backtest_strategy(
        start_date=start_date,
        end_date=end_date,
        initial_capital=100000
    )

    if "error" in result:
        print(f"回測失敗: {result['error']}")
        return

    # 顯示結果
    perf = result["performance"]
    print(f"\n📊 回測績效")
    print(f"  初始資金: ${perf['initial_capital']:,.0f}")
    print(f"  期末價值: ${perf['final_value']:,.0f}")
    print(f"  總報酬率: {perf['total_return_pct']:.2f}%")
    print(f"  年化報酬: {perf['annualized_return_pct']:.2f}%")
    print(f"  最大回撤: {perf['max_drawdown_pct']:.2f}%")
    print(f"  夏普比率: {perf['sharpe_ratio']:.2f}")
    print(f"  勝率: {perf['win_rate_pct']:.1f}%")
    print(f"  總交易次數: {perf['total_trades']}")

    print(f"\n📈 各週期績效")
    for phase, data in result["phase_performance"].items():
        phase_info = MARKET_CYCLES.get(phase, {})
        emoji = phase_info.get("emoji", "")
        print(f"  {emoji} {phase}: {data['months']} 個月, 平均月報酬 {data['avg_return']:.2f}%")

    print(f"\n🔄 週期變化記錄")
    for change in result["cycle_changes"]:
        print(f"  {change['date']}: {change['from_phase']} → {change['to_phase']} (分數: {change['score']:.2f})")

    # 比較基準
    benchmark = backtester.compare_with_benchmark(start_date, end_date)
    if "error" not in benchmark:
        print(f"\n📌 與基準比較 (SPY 買入持有)")
        print(f"  SPY 報酬率: {benchmark['total_return_pct']:.2f}%")
        alpha = perf['total_return_pct'] - benchmark['total_return_pct']
        print(f"  超額報酬 (Alpha): {alpha:.2f}%")


if __name__ == "__main__":
    main()
