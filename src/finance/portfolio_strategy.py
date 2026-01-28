"""
投資組合策略模組
- Buy and Hold 買入持有策略
- Momentum Rotation 動態換股策略
- Volatility-Adjusted Momentum 波動率校正動態策略
- Walk-Forward Analysis 走動式評估
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta, date
from typing import Dict, List, Tuple, Optional
import sqlite3
from pathlib import Path
import logging

logger = logging.getLogger(__name__)


class PortfolioStrategy:
    """投資組合策略分析器"""

    def __init__(self, db_path: str = "finance.db"):
        self.db_path = Path(db_path)

    def get_all_prices(self, symbols: List[str], days: int = 365,
                        start_date: str = None, end_date: str = None) -> pd.DataFrame:
        """取得多檔股票的價格數據"""
        conn = sqlite3.connect(self.db_path)

        all_data = []
        for symbol in symbols:
            if start_date and end_date:
                query = """
                    SELECT date, symbol, close, volume
                    FROM daily_prices
                    WHERE symbol = ? AND date >= ? AND date <= ?
                    ORDER BY date ASC
                """
                df = pd.read_sql_query(query, conn, params=(symbol, start_date, end_date))
            else:
                query = """
                    SELECT date, symbol, close, volume
                    FROM daily_prices
                    WHERE symbol = ?
                    ORDER BY date DESC
                    LIMIT ?
                """
                df = pd.read_sql_query(query, conn, params=(symbol, days))
            if not df.empty:
                all_data.append(df)

        conn.close()

        if not all_data:
            return pd.DataFrame()

        combined = pd.concat(all_data, ignore_index=True)
        combined['date'] = pd.to_datetime(combined['date'])

        # 轉換為寬表格 (日期為index, 股票為columns)
        pivot = combined.pivot_table(index='date', columns='symbol', values='close')
        pivot = pivot.sort_index()

        return pivot

    def get_watchlist_symbols(self, market: str = None) -> List[str]:
        """取得追蹤清單的股票代碼"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        if market:
            cursor.execute(
                "SELECT symbol FROM watchlist WHERE is_active = 1 AND market = ?",
                (market,)
            )
        else:
            cursor.execute("SELECT symbol FROM watchlist WHERE is_active = 1")

        symbols = [row[0] for row in cursor.fetchall()]
        conn.close()
        return symbols

    def buy_and_hold(self, symbol: str, initial_capital: float = 100000,
                     start_date: str = None, end_date: str = None) -> Dict:
        """
        買入持有策略

        在第一天買入，持有到最後一天

        Args:
            symbol: 股票代碼
            initial_capital: 初始資金
            start_date: 開始日期 (YYYY-MM-DD)
            end_date: 結束日期 (YYYY-MM-DD)
        """
        conn = sqlite3.connect(self.db_path)

        query = """
            SELECT date, open, high, low, close, volume
            FROM daily_prices
            WHERE symbol = ?
        """
        params = [symbol]

        if start_date:
            query += " AND date >= ?"
            params.append(start_date)
        if end_date:
            query += " AND date <= ?"
            params.append(end_date)

        query += " ORDER BY date ASC"

        df = pd.read_sql_query(query, conn, params=params)
        conn.close()

        if df.empty or len(df) < 2:
            return {
                'trades': [],
                'equity_curve': [],
                'summary': {'total_return_pct': 0},
                'df': pd.DataFrame()
            }

        df['date'] = pd.to_datetime(df['date'])

        # 第一天買入
        entry_date = df.iloc[0]['date']
        entry_price = df.iloc[0]['close']
        shares = int(initial_capital / entry_price)
        cost = shares * entry_price
        remaining_cash = initial_capital - cost

        # 最後一天結算
        exit_date = df.iloc[-1]['date']
        exit_price = df.iloc[-1]['close']
        final_value = shares * exit_price + remaining_cash

        profit = final_value - initial_capital
        profit_pct = (final_value - initial_capital) / initial_capital * 100

        # 建立權益曲線
        equity_curve = []
        for _, row in df.iterrows():
            equity = shares * row['close'] + remaining_cash
            equity_curve.append({
                'date': row['date'],
                'equity': equity,
                'position': 1
            })

        # 交易記錄
        trades = [{
            'entry_date': entry_date.strftime('%Y-%m-%d'),
            'entry_price': entry_price,
            'exit_date': exit_date.strftime('%Y-%m-%d'),
            'exit_price': exit_price,
            'shares': shares,
            'profit': profit,
            'profit_pct': profit_pct,
            'capital_after': final_value
        }]

        # 計算統計
        returns = df['close'].pct_change().dropna()
        max_price = df['close'].cummax()
        drawdown = (df['close'] - max_price) / max_price
        max_drawdown = drawdown.min() * 100

        summary = {
            'strategy': 'Buy and Hold',
            'initial_capital': initial_capital,
            'final_equity': final_value,
            'total_profit': profit,
            'total_return_pct': profit_pct,
            'total_trades': 1,
            'holding_days': len(df),
            'max_drawdown': max_drawdown,
            'sharpe_ratio': (returns.mean() / returns.std()) * np.sqrt(252) if returns.std() > 0 else 0,
            'volatility': returns.std() * np.sqrt(252) * 100
        }

        return {
            'trades': trades,
            'equity_curve': equity_curve,
            'summary': summary,
            'df': df
        }

    def momentum_rotation(self, symbols: List[str] = None,
                          initial_capital: float = 100000,
                          top_n: int = 5,
                          rebalance_days: int = 20,
                          lookback_days: int = 20,
                          market: str = 'US',
                          start_date: str = None,
                          end_date: str = None) -> Dict:
        """
        動態換股策略 (動量輪動)

        Args:
            symbols: 股票池 (None則使用追蹤清單)
            initial_capital: 初始資金
            top_n: 選擇動量最強的前N檔
            rebalance_days: 每N天重新調整
            lookback_days: 計算動量的回顧天數
            market: 市場篩選
            start_date: 開始日期 (YYYY-MM-DD)
            end_date: 結束日期 (YYYY-MM-DD)

        策略邏輯:
        1. 計算每檔股票過去N天的報酬率 (動量)
        2. 選擇動量最強的前K檔股票
        3. 平均分配資金
        4. 每M天重新計算並調整持股
        """
        if symbols is None:
            symbols = self.get_watchlist_symbols(market=market)

        if not symbols:
            return {'error': '沒有可用的股票'}

        # 取得所有價格數據
        prices = self.get_all_prices(symbols, days=400, start_date=start_date, end_date=end_date)

        if prices.empty:
            return {'error': '無法取得價格數據'}

        # 移除有太多缺失值的股票
        valid_symbols = prices.columns[prices.notna().sum() > len(prices) * 0.8].tolist()
        prices = prices[valid_symbols].dropna()

        if len(valid_symbols) < top_n:
            return {'error': f'有效股票數量({len(valid_symbols)})不足，需要至少{top_n}檔'}

        # 計算每日報酬率
        returns = prices.pct_change()

        # 初始化
        capital = initial_capital
        holdings = {}  # {symbol: shares}
        equity_curve = []
        trades = []
        rebalance_records = []

        dates = prices.index.tolist()

        for i, date in enumerate(dates):
            if i < lookback_days:
                equity_curve.append({
                    'date': date,
                    'equity': capital,
                    'holdings': {}
                })
                continue

            # 計算當前持倉價值
            current_value = capital
            for sym, shares in holdings.items():
                if sym in prices.columns and pd.notna(prices.loc[date, sym]):
                    current_value += shares * prices.loc[date, sym]

            # 是否需要重新平衡
            should_rebalance = (i == lookback_days) or (i % rebalance_days == 0)

            if should_rebalance:
                # 計算動量 (過去N天報酬率)
                momentum = {}
                for sym in valid_symbols:
                    if i >= lookback_days:
                        start_price = prices.loc[dates[i - lookback_days], sym]
                        end_price = prices.loc[date, sym]
                        if pd.notna(start_price) and pd.notna(end_price) and start_price > 0:
                            momentum[sym] = (end_price - start_price) / start_price
                        else:
                            momentum[sym] = -999

                # 排序選擇前N檔
                sorted_momentum = sorted(momentum.items(), key=lambda x: x[1], reverse=True)
                selected = [sym for sym, _ in sorted_momentum[:top_n] if momentum[sym] > -999]

                if not selected:
                    continue

                # 賣出不在新名單中的股票
                for sym in list(holdings.keys()):
                    if sym not in selected and holdings[sym] > 0:
                        sell_price = prices.loc[date, sym]
                        if pd.notna(sell_price):
                            sell_value = holdings[sym] * sell_price
                            capital += sell_value
                            trades.append({
                                'date': date.strftime('%Y-%m-%d'),
                                'action': 'SELL',
                                'symbol': sym,
                                'shares': holdings[sym],
                                'price': sell_price,
                                'value': sell_value,
                                'reason': '動量排名下降'
                            })
                            holdings[sym] = 0

                # 計算可投資金額
                total_value = capital
                for sym, shares in holdings.items():
                    if shares > 0 and pd.notna(prices.loc[date, sym]):
                        total_value += shares * prices.loc[date, sym]

                # 目標配置
                target_value_per_stock = total_value / len(selected)

                # 調整持倉
                new_holdings = {}
                for sym in selected:
                    current_price = prices.loc[date, sym]
                    if pd.notna(current_price) and current_price > 0:
                        current_shares = holdings.get(sym, 0)
                        current_holding_value = current_shares * current_price
                        target_shares = int(target_value_per_stock / current_price)

                        if target_shares > current_shares:
                            # 買入
                            buy_shares = target_shares - current_shares
                            buy_value = buy_shares * current_price
                            if buy_value <= capital:
                                capital -= buy_value
                                new_holdings[sym] = target_shares
                                trades.append({
                                    'date': date.strftime('%Y-%m-%d'),
                                    'action': 'BUY',
                                    'symbol': sym,
                                    'shares': buy_shares,
                                    'price': current_price,
                                    'value': buy_value,
                                    'reason': f'動量排名 Top{top_n}',
                                    'momentum': f"{momentum[sym]*100:.1f}%"
                                })
                            else:
                                new_holdings[sym] = current_shares
                        elif target_shares < current_shares:
                            # 部分賣出
                            sell_shares = current_shares - target_shares
                            sell_value = sell_shares * current_price
                            capital += sell_value
                            new_holdings[sym] = target_shares
                            trades.append({
                                'date': date.strftime('%Y-%m-%d'),
                                'action': 'SELL',
                                'symbol': sym,
                                'shares': sell_shares,
                                'price': current_price,
                                'value': sell_value,
                                'reason': '調整配置'
                            })
                        else:
                            new_holdings[sym] = current_shares

                holdings = {k: v for k, v in new_holdings.items() if v > 0}

                # 記錄換股
                rebalance_records.append({
                    'date': date.strftime('%Y-%m-%d'),
                    'selected': selected,
                    'momentum': {s: f"{momentum[s]*100:.1f}%" for s in selected},
                    'total_value': total_value
                })

            # 記錄權益
            final_equity = capital
            holdings_detail = {}
            for sym, shares in holdings.items():
                if shares > 0 and sym in prices.columns and pd.notna(prices.loc[date, sym]):
                    value = shares * prices.loc[date, sym]
                    final_equity += value
                    holdings_detail[sym] = {
                        'shares': shares,
                        'price': prices.loc[date, sym],
                        'value': value
                    }

            equity_curve.append({
                'date': date,
                'equity': final_equity,
                'cash': capital,
                'holdings': holdings_detail
            })

        # 計算總結
        if equity_curve:
            final_equity = equity_curve[-1]['equity']
            total_return = (final_equity - initial_capital) / initial_capital * 100

            # 計算每日報酬
            equity_series = pd.Series([e['equity'] for e in equity_curve])
            daily_returns = equity_series.pct_change().dropna()

            max_equity = equity_series.cummax()
            drawdown = (equity_series - max_equity) / max_equity
            max_drawdown = drawdown.min() * 100

            # 買入持有基準 (用等權重投資所有股票)
            if len(valid_symbols) > 0:
                buy_hold_return = prices[valid_symbols].iloc[-1].mean() / prices[valid_symbols].iloc[0].mean() - 1
                buy_hold_return *= 100
            else:
                buy_hold_return = 0

            summary = {
                'strategy': 'Momentum Rotation',
                'initial_capital': initial_capital,
                'final_equity': final_equity,
                'total_profit': final_equity - initial_capital,
                'total_return_pct': total_return,
                'total_trades': len(trades),
                'rebalance_count': len(rebalance_records),
                'top_n': top_n,
                'rebalance_days': rebalance_days,
                'lookback_days': lookback_days,
                'max_drawdown': max_drawdown,
                'sharpe_ratio': (daily_returns.mean() / daily_returns.std()) * np.sqrt(252) if daily_returns.std() > 0 else 0,
                'buy_hold_return': buy_hold_return,
                'final_holdings': holdings,
                'stock_pool_size': len(valid_symbols)
            }
        else:
            summary = {'error': '無法計算'}

        return {
            'trades': trades,
            'equity_curve': equity_curve,
            'rebalance_records': rebalance_records,
            'summary': summary
        }

    def momentum_rotation_vol_adjusted(self, symbols: List[str] = None,
                                        initial_capital: float = 100000,
                                        top_n: int = 5,
                                        rebalance_days: int = 20,
                                        lookback_days: int = 20,
                                        market: str = 'US',
                                        start_date: str = None,
                                        end_date: str = None,
                                        vol_adjust_method: str = 'sharpe') -> Dict:
        """
        波動率校正的動態換股策略

        Args:
            vol_adjust_method: 波動率校正方法
                - 'sharpe': 夏普比率 (return / volatility)
                - 'sortino': 索提諾比率 (return / downside_volatility)
                - 'vol_scaled': 波動率縮放 (return / volatility * target_vol)
        """
        if symbols is None:
            symbols = self.get_watchlist_symbols(market=market)

        if not symbols:
            return {'error': '沒有可用的股票'}

        # 取得所有價格數據
        prices = self.get_all_prices(symbols, days=500, start_date=start_date, end_date=end_date)

        if prices.empty:
            return {'error': '無法取得價格數據'}

        # 移除有太多缺失值的股票
        valid_symbols = prices.columns[prices.notna().sum() > len(prices) * 0.8].tolist()
        prices = prices[valid_symbols].dropna()

        if len(valid_symbols) < top_n:
            return {'error': f'有效股票數量({len(valid_symbols)})不足，需要至少{top_n}檔'}

        # 計算每日報酬率
        returns = prices.pct_change()

        # 初始化
        capital = initial_capital
        holdings = {}
        equity_curve = []
        trades = []
        rebalance_records = []

        dates = prices.index.tolist()
        target_volatility = 0.15 / np.sqrt(252)  # 目標年化波動率 15%

        for i, current_date in enumerate(dates):
            if i < lookback_days:
                equity_curve.append({
                    'date': current_date,
                    'equity': capital,
                    'holdings': {}
                })
                continue

            # 計算當前持倉價值
            current_value = capital
            for sym, shares in holdings.items():
                if sym in prices.columns and pd.notna(prices.loc[current_date, sym]):
                    current_value += shares * prices.loc[current_date, sym]

            # 是否需要重新平衡
            should_rebalance = (i == lookback_days) or (i % rebalance_days == 0)

            if should_rebalance:
                # 計算波動率校正的動量
                adjusted_momentum = {}

                for sym in valid_symbols:
                    if i >= lookback_days:
                        # 取得回顧期間的報酬
                        lookback_returns = returns[sym].iloc[i - lookback_days:i]

                        if len(lookback_returns) < lookback_days * 0.8:
                            adjusted_momentum[sym] = -999
                            continue

                        # 計算原始動量 (累計報酬率)
                        raw_momentum = (1 + lookback_returns).prod() - 1

                        # 計算波動率
                        volatility = lookback_returns.std()

                        if volatility > 0:
                            if vol_adjust_method == 'sharpe':
                                # 夏普比率: 報酬 / 波動率
                                adjusted_momentum[sym] = raw_momentum / volatility
                            elif vol_adjust_method == 'sortino':
                                # 索提諾比率: 報酬 / 下行波動率
                                downside_returns = lookback_returns[lookback_returns < 0]
                                downside_vol = downside_returns.std() if len(downside_returns) > 0 else volatility
                                adjusted_momentum[sym] = raw_momentum / downside_vol if downside_vol > 0 else raw_momentum / volatility
                            elif vol_adjust_method == 'vol_scaled':
                                # 波動率縮放: 調整到目標波動率
                                scale_factor = target_volatility / volatility
                                adjusted_momentum[sym] = raw_momentum * scale_factor
                            else:
                                adjusted_momentum[sym] = raw_momentum
                        else:
                            adjusted_momentum[sym] = raw_momentum

                # 排序選擇前N檔
                sorted_momentum = sorted(adjusted_momentum.items(), key=lambda x: x[1], reverse=True)
                selected = [sym for sym, _ in sorted_momentum[:top_n] if adjusted_momentum[sym] > -999]

                if not selected:
                    continue

                # 賣出不在新名單中的股票
                for sym in list(holdings.keys()):
                    if sym not in selected and holdings[sym] > 0:
                        sell_price = prices.loc[current_date, sym]
                        if pd.notna(sell_price):
                            sell_value = holdings[sym] * sell_price
                            capital += sell_value
                            trades.append({
                                'date': current_date.strftime('%Y-%m-%d'),
                                'action': 'SELL',
                                'symbol': sym,
                                'shares': holdings[sym],
                                'price': sell_price,
                                'value': sell_value,
                                'reason': '波動率校正排名下降'
                            })
                            holdings[sym] = 0

                # 計算可投資金額
                total_value = capital
                for sym, shares in holdings.items():
                    if shares > 0 and pd.notna(prices.loc[current_date, sym]):
                        total_value += shares * prices.loc[current_date, sym]

                # 目標配置 (可以根據波動率做加權，但先用等權重)
                target_value_per_stock = total_value / len(selected)

                # 調整持倉
                new_holdings = {}
                for sym in selected:
                    current_price = prices.loc[current_date, sym]
                    if pd.notna(current_price) and current_price > 0:
                        current_shares = holdings.get(sym, 0)
                        target_shares = int(target_value_per_stock / current_price)

                        if target_shares > current_shares:
                            buy_shares = target_shares - current_shares
                            buy_value = buy_shares * current_price
                            if buy_value <= capital:
                                capital -= buy_value
                                new_holdings[sym] = target_shares
                                trades.append({
                                    'date': current_date.strftime('%Y-%m-%d'),
                                    'action': 'BUY',
                                    'symbol': sym,
                                    'shares': buy_shares,
                                    'price': current_price,
                                    'value': buy_value,
                                    'reason': f'波動率校正 Top{top_n}',
                                    'adjusted_momentum': f"{adjusted_momentum[sym]:.3f}"
                                })
                            else:
                                new_holdings[sym] = current_shares
                        elif target_shares < current_shares:
                            sell_shares = current_shares - target_shares
                            sell_value = sell_shares * current_price
                            capital += sell_value
                            new_holdings[sym] = target_shares
                            trades.append({
                                'date': current_date.strftime('%Y-%m-%d'),
                                'action': 'SELL',
                                'symbol': sym,
                                'shares': sell_shares,
                                'price': current_price,
                                'value': sell_value,
                                'reason': '調整配置'
                            })
                        else:
                            new_holdings[sym] = current_shares

                holdings = {k: v for k, v in new_holdings.items() if v > 0}

                rebalance_records.append({
                    'date': current_date.strftime('%Y-%m-%d'),
                    'selected': selected,
                    'adjusted_momentum': {s: f"{adjusted_momentum[s]:.3f}" for s in selected},
                    'total_value': total_value
                })

            # 記錄權益
            final_equity = capital
            holdings_detail = {}
            for sym, shares in holdings.items():
                if shares > 0 and sym in prices.columns and pd.notna(prices.loc[current_date, sym]):
                    value = shares * prices.loc[current_date, sym]
                    final_equity += value
                    holdings_detail[sym] = {
                        'shares': shares,
                        'price': prices.loc[current_date, sym],
                        'value': value
                    }

            equity_curve.append({
                'date': current_date,
                'equity': final_equity,
                'cash': capital,
                'holdings': holdings_detail
            })

        # 計算總結
        if equity_curve:
            final_equity = equity_curve[-1]['equity']
            total_return = (final_equity - initial_capital) / initial_capital * 100

            equity_series = pd.Series([e['equity'] for e in equity_curve])
            daily_returns = equity_series.pct_change().dropna()

            max_equity = equity_series.cummax()
            drawdown = (equity_series - max_equity) / max_equity
            max_drawdown = drawdown.min() * 100

            if len(valid_symbols) > 0:
                buy_hold_return = prices[valid_symbols].iloc[-1].mean() / prices[valid_symbols].iloc[0].mean() - 1
                buy_hold_return *= 100
            else:
                buy_hold_return = 0

            summary = {
                'strategy': f'Momentum Rotation (Vol-Adjusted: {vol_adjust_method})',
                'vol_adjust_method': vol_adjust_method,
                'initial_capital': initial_capital,
                'final_equity': final_equity,
                'total_profit': final_equity - initial_capital,
                'total_return_pct': total_return,
                'total_trades': len(trades),
                'rebalance_count': len(rebalance_records),
                'top_n': top_n,
                'rebalance_days': rebalance_days,
                'lookback_days': lookback_days,
                'max_drawdown': max_drawdown,
                'sharpe_ratio': (daily_returns.mean() / daily_returns.std()) * np.sqrt(252) if daily_returns.std() > 0 else 0,
                'buy_hold_return': buy_hold_return,
                'final_holdings': holdings,
                'stock_pool_size': len(valid_symbols)
            }
        else:
            summary = {'error': '無法計算'}

        return {
            'trades': trades,
            'equity_curve': equity_curve,
            'rebalance_records': rebalance_records,
            'summary': summary
        }

    def walk_forward_analysis(self, symbols: List[str] = None,
                               initial_capital: float = 100000,
                               market: str = 'US',
                               start_date: str = None,
                               end_date: str = None,
                               train_months: int = 6,
                               test_months: int = 3,
                               vol_adjusted: bool = True) -> Dict:
        """
        走動式評估 (Walk-Forward Analysis)

        將數據分成多個訓練/測試期，在訓練期優化參數，在測試期驗證

        Args:
            train_months: 訓練期月數
            test_months: 測試期月數
            vol_adjusted: 是否使用波動率校正
        """
        if symbols is None:
            symbols = self.get_watchlist_symbols(market=market)

        if not symbols:
            return {'error': '沒有可用的股票'}

        # 取得所有價格數據
        prices = self.get_all_prices(symbols, days=2000, start_date=start_date, end_date=end_date)

        if prices.empty:
            return {'error': '無法取得價格數據'}

        # 參數搜索空間
        param_grid = {
            'top_n': [3, 5, 7],
            'rebalance_days': [10, 20, 30],
            'lookback_days': [10, 20, 30]
        }

        # 分割時間窗口
        dates = prices.index.tolist()
        if len(dates) < (train_months + test_months) * 21:
            return {'error': '數據不足以進行走動式評估'}

        window_results = []
        all_test_equity = []
        combined_trades = []

        # 每個窗口大約 21 個交易日
        train_days = train_months * 21
        test_days = test_months * 21
        step_days = test_days  # 每次前進一個測試期

        start_idx = 0

        while start_idx + train_days + test_days <= len(dates):
            train_start = dates[start_idx]
            train_end = dates[start_idx + train_days - 1]
            test_start = dates[start_idx + train_days]
            test_end = dates[min(start_idx + train_days + test_days - 1, len(dates) - 1)]

            logger.info(f"Window: Train {train_start.strftime('%Y-%m-%d')} to {train_end.strftime('%Y-%m-%d')}, "
                       f"Test {test_start.strftime('%Y-%m-%d')} to {test_end.strftime('%Y-%m-%d')}")

            # 在訓練期搜索最佳參數
            best_params = None
            best_sharpe = -999

            train_prices = prices.loc[train_start:train_end]

            for top_n in param_grid['top_n']:
                for rebal in param_grid['rebalance_days']:
                    for lookback in param_grid['lookback_days']:
                        try:
                            if vol_adjusted:
                                result = self._run_momentum_on_prices(
                                    train_prices, initial_capital, top_n, rebal, lookback,
                                    vol_adjusted=True
                                )
                            else:
                                result = self._run_momentum_on_prices(
                                    train_prices, initial_capital, top_n, rebal, lookback,
                                    vol_adjusted=False
                                )

                            if 'summary' in result and 'sharpe_ratio' in result['summary']:
                                sharpe = result['summary']['sharpe_ratio']
                                if sharpe > best_sharpe:
                                    best_sharpe = sharpe
                                    best_params = {'top_n': top_n, 'rebalance_days': rebal, 'lookback_days': lookback}
                        except Exception as e:
                            continue

            if best_params is None:
                best_params = {'top_n': 5, 'rebalance_days': 20, 'lookback_days': 20}

            # 在測試期使用最佳參數
            test_prices = prices.loc[test_start:test_end]

            if vol_adjusted:
                test_result = self._run_momentum_on_prices(
                    test_prices, initial_capital, best_params['top_n'],
                    best_params['rebalance_days'], best_params['lookback_days'],
                    vol_adjusted=True
                )
            else:
                test_result = self._run_momentum_on_prices(
                    test_prices, initial_capital, best_params['top_n'],
                    best_params['rebalance_days'], best_params['lookback_days'],
                    vol_adjusted=False
                )

            window_results.append({
                'train_period': f"{train_start.strftime('%Y-%m-%d')} ~ {train_end.strftime('%Y-%m-%d')}",
                'test_period': f"{test_start.strftime('%Y-%m-%d')} ~ {test_end.strftime('%Y-%m-%d')}",
                'best_params': best_params,
                'train_sharpe': best_sharpe,
                'test_return': test_result.get('summary', {}).get('total_return_pct', 0),
                'test_sharpe': test_result.get('summary', {}).get('sharpe_ratio', 0),
                'test_max_dd': test_result.get('summary', {}).get('max_drawdown', 0)
            })

            if 'equity_curve' in test_result:
                all_test_equity.extend(test_result['equity_curve'])
            if 'trades' in test_result:
                combined_trades.extend(test_result['trades'])

            start_idx += step_days

        # 計算整體統計
        if window_results:
            avg_test_return = np.mean([w['test_return'] for w in window_results])
            avg_test_sharpe = np.mean([w['test_sharpe'] for w in window_results])
            avg_test_dd = np.mean([w['test_max_dd'] for w in window_results])

            # 計算一致性
            positive_windows = sum(1 for w in window_results if w['test_return'] > 0)
            consistency = positive_windows / len(window_results) * 100

            summary = {
                'strategy': 'Walk-Forward Analysis',
                'vol_adjusted': vol_adjusted,
                'train_months': train_months,
                'test_months': test_months,
                'total_windows': len(window_results),
                'avg_test_return_pct': avg_test_return,
                'avg_test_sharpe': avg_test_sharpe,
                'avg_test_max_dd': avg_test_dd,
                'consistency_pct': consistency,
                'positive_windows': positive_windows
            }
        else:
            summary = {'error': '無法完成走動式評估'}

        return {
            'window_results': window_results,
            'summary': summary,
            'equity_curve': all_test_equity,
            'trades': combined_trades
        }

    def _run_momentum_on_prices(self, prices: pd.DataFrame, initial_capital: float,
                                 top_n: int, rebalance_days: int, lookback_days: int,
                                 vol_adjusted: bool = False) -> Dict:
        """在給定價格數據上運行動量策略 (內部方法)"""
        valid_symbols = prices.columns[prices.notna().sum() > len(prices) * 0.5].tolist()
        prices = prices[valid_symbols].dropna()

        if len(valid_symbols) < top_n:
            return {'error': 'Not enough stocks', 'summary': {'sharpe_ratio': -999}}

        returns = prices.pct_change()

        capital = initial_capital
        holdings = {}
        equity_curve = []
        trades = []

        dates = prices.index.tolist()
        target_volatility = 0.15 / np.sqrt(252)

        for i, current_date in enumerate(dates):
            if i < lookback_days:
                equity_curve.append({'date': current_date, 'equity': capital})
                continue

            current_value = capital
            for sym, shares in holdings.items():
                if sym in prices.columns and pd.notna(prices.loc[current_date, sym]):
                    current_value += shares * prices.loc[current_date, sym]

            should_rebalance = (i == lookback_days) or (i % rebalance_days == 0)

            if should_rebalance:
                momentum = {}
                for sym in valid_symbols:
                    lookback_returns = returns[sym].iloc[i - lookback_days:i]
                    if len(lookback_returns) < lookback_days * 0.5:
                        momentum[sym] = -999
                        continue

                    raw_momentum = (1 + lookback_returns).prod() - 1
                    volatility = lookback_returns.std()

                    if vol_adjusted and volatility > 0:
                        momentum[sym] = raw_momentum / volatility
                    else:
                        momentum[sym] = raw_momentum

                sorted_momentum = sorted(momentum.items(), key=lambda x: x[1], reverse=True)
                selected = [sym for sym, _ in sorted_momentum[:top_n] if momentum[sym] > -999]

                if not selected:
                    continue

                # 清倉不在名單中的股票
                for sym in list(holdings.keys()):
                    if sym not in selected and holdings[sym] > 0:
                        sell_price = prices.loc[current_date, sym]
                        if pd.notna(sell_price):
                            capital += holdings[sym] * sell_price
                            holdings[sym] = 0

                total_value = capital
                for sym, shares in holdings.items():
                    if shares > 0 and pd.notna(prices.loc[current_date, sym]):
                        total_value += shares * prices.loc[current_date, sym]

                target_per_stock = total_value / len(selected)

                new_holdings = {}
                for sym in selected:
                    current_price = prices.loc[current_date, sym]
                    if pd.notna(current_price) and current_price > 0:
                        current_shares = holdings.get(sym, 0)
                        target_shares = int(target_per_stock / current_price)

                        if target_shares > current_shares:
                            buy_shares = target_shares - current_shares
                            buy_value = buy_shares * current_price
                            if buy_value <= capital:
                                capital -= buy_value
                                new_holdings[sym] = target_shares
                        elif target_shares < current_shares:
                            sell_shares = current_shares - target_shares
                            capital += sell_shares * current_price
                            new_holdings[sym] = target_shares
                        else:
                            new_holdings[sym] = current_shares

                holdings = {k: v for k, v in new_holdings.items() if v > 0}

            final_equity = capital
            for sym, shares in holdings.items():
                if shares > 0 and sym in prices.columns and pd.notna(prices.loc[current_date, sym]):
                    final_equity += shares * prices.loc[current_date, sym]

            equity_curve.append({'date': current_date, 'equity': final_equity})

        if equity_curve:
            final_equity = equity_curve[-1]['equity']
            total_return = (final_equity - initial_capital) / initial_capital * 100

            equity_series = pd.Series([e['equity'] for e in equity_curve])
            daily_returns = equity_series.pct_change().dropna()

            max_equity = equity_series.cummax()
            drawdown = (equity_series - max_equity) / max_equity
            max_drawdown = drawdown.min() * 100

            sharpe = (daily_returns.mean() / daily_returns.std()) * np.sqrt(252) if daily_returns.std() > 0 else 0

            summary = {
                'total_return_pct': total_return,
                'max_drawdown': max_drawdown,
                'sharpe_ratio': sharpe,
                'final_equity': final_equity
            }
        else:
            summary = {'sharpe_ratio': -999}

        return {'equity_curve': equity_curve, 'trades': trades, 'summary': summary}

    def robustness_test(self, symbols: List[str] = None,
                         initial_capital: float = 100000,
                         market: str = 'US',
                         start_date: str = None,
                         end_date: str = None) -> Dict:
        """
        魯棒性檢測

        測試策略在不同參數組合下的穩定性

        Returns:
            包含參數敏感度分析、最佳參數、穩定性指標
        """
        if symbols is None:
            symbols = self.get_watchlist_symbols(market=market)

        if not symbols:
            return {'error': '沒有可用的股票'}

        prices = self.get_all_prices(symbols, days=1500, start_date=start_date, end_date=end_date)

        if prices.empty:
            return {'error': '無法取得價格數據'}

        # 參數網格
        param_results = []

        top_n_range = [3, 5, 7, 10]
        rebalance_range = [5, 10, 20, 30, 40]
        lookback_range = [10, 20, 30, 40]

        total_tests = len(top_n_range) * len(rebalance_range) * len(lookback_range)
        test_count = 0

        for top_n in top_n_range:
            for rebal in rebalance_range:
                for lookback in lookback_range:
                    test_count += 1

                    try:
                        # 測試原始動量
                        result_raw = self._run_momentum_on_prices(
                            prices, initial_capital, top_n, rebal, lookback,
                            vol_adjusted=False
                        )

                        # 測試波動率校正
                        result_vol = self._run_momentum_on_prices(
                            prices, initial_capital, top_n, rebal, lookback,
                            vol_adjusted=True
                        )

                        param_results.append({
                            'top_n': top_n,
                            'rebalance_days': rebal,
                            'lookback_days': lookback,
                            'raw_return': result_raw.get('summary', {}).get('total_return_pct', 0),
                            'raw_sharpe': result_raw.get('summary', {}).get('sharpe_ratio', 0),
                            'raw_max_dd': result_raw.get('summary', {}).get('max_drawdown', 0),
                            'vol_return': result_vol.get('summary', {}).get('total_return_pct', 0),
                            'vol_sharpe': result_vol.get('summary', {}).get('sharpe_ratio', 0),
                            'vol_max_dd': result_vol.get('summary', {}).get('max_drawdown', 0)
                        })

                    except Exception as e:
                        continue

        if not param_results:
            return {'error': '無法完成魯棒性檢測'}

        # 分析結果
        df = pd.DataFrame(param_results)

        # 找最佳參數
        best_raw_idx = df['raw_sharpe'].idxmax()
        best_vol_idx = df['vol_sharpe'].idxmax()

        best_raw_params = {
            'top_n': df.loc[best_raw_idx, 'top_n'],
            'rebalance_days': df.loc[best_raw_idx, 'rebalance_days'],
            'lookback_days': df.loc[best_raw_idx, 'lookback_days']
        }

        best_vol_params = {
            'top_n': df.loc[best_vol_idx, 'top_n'],
            'rebalance_days': df.loc[best_vol_idx, 'rebalance_days'],
            'lookback_days': df.loc[best_vol_idx, 'lookback_days']
        }

        # 計算穩定性指標
        raw_return_std = df['raw_return'].std()
        vol_return_std = df['vol_return'].std()
        raw_sharpe_std = df['raw_sharpe'].std()
        vol_sharpe_std = df['vol_sharpe'].std()

        # 正報酬比例
        raw_positive_pct = (df['raw_return'] > 0).sum() / len(df) * 100
        vol_positive_pct = (df['vol_return'] > 0).sum() / len(df) * 100

        # 參數敏感度分析
        sensitivity = {
            'top_n': df.groupby('top_n').agg({
                'raw_sharpe': 'mean', 'vol_sharpe': 'mean'
            }).to_dict(),
            'rebalance_days': df.groupby('rebalance_days').agg({
                'raw_sharpe': 'mean', 'vol_sharpe': 'mean'
            }).to_dict(),
            'lookback_days': df.groupby('lookback_days').agg({
                'raw_sharpe': 'mean', 'vol_sharpe': 'mean'
            }).to_dict()
        }

        summary = {
            'total_tests': len(param_results),
            'best_raw_params': best_raw_params,
            'best_raw_sharpe': df.loc[best_raw_idx, 'raw_sharpe'],
            'best_raw_return': df.loc[best_raw_idx, 'raw_return'],
            'best_vol_params': best_vol_params,
            'best_vol_sharpe': df.loc[best_vol_idx, 'vol_sharpe'],
            'best_vol_return': df.loc[best_vol_idx, 'vol_return'],
            'avg_raw_return': df['raw_return'].mean(),
            'avg_vol_return': df['vol_return'].mean(),
            'avg_raw_sharpe': df['raw_sharpe'].mean(),
            'avg_vol_sharpe': df['vol_sharpe'].mean(),
            'raw_return_stability': raw_return_std,
            'vol_return_stability': vol_return_std,
            'raw_positive_pct': raw_positive_pct,
            'vol_positive_pct': vol_positive_pct,
            'vol_adjustment_benefit': df['vol_sharpe'].mean() - df['raw_sharpe'].mean()
        }

        return {
            'param_results': param_results,
            'sensitivity': sensitivity,
            'summary': summary
        }

    def compare_strategies(self, symbol: str, initial_capital: float = 100000) -> Dict:
        """比較單一股票的所有策略"""
        from .analyzer import TechnicalAnalyzer

        analyzer = TechnicalAnalyzer(str(self.db_path))

        results = {}

        # Buy and Hold
        bh = self.buy_and_hold(symbol, initial_capital)
        results['Buy and Hold'] = bh['summary']

        # 技術指標策略
        for strategy in ['MA', 'RSI', 'MACD', 'BB']:
            th = analyzer.get_trade_history(symbol, strategy, initial_capital)
            results[f'{strategy} 策略'] = th['summary']

        return results
