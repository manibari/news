"""
股票技術分析與回測模組
"""

import pandas as pd
import numpy as np
from datetime import date, timedelta
from typing import Dict, List, Tuple, Optional
import sqlite3
from pathlib import Path


class TechnicalAnalyzer:
    """技術分析器"""

    def __init__(self, db_path: str = "finance.db"):
        self.db_path = Path(db_path)

    def get_price_data(self, symbol: str, days: int = 365,
                        start_date: str = None, end_date: str = None) -> pd.DataFrame:
        """取得股票價格數據

        Args:
            symbol: 股票代碼
            days: 取最近N天 (當 start_date 為 None 時使用)
            start_date: 開始日期 (YYYY-MM-DD)
            end_date: 結束日期 (YYYY-MM-DD)
        """
        conn = sqlite3.connect(self.db_path)

        if start_date and end_date:
            query = """
                SELECT date, open, high, low, close, volume
                FROM daily_prices
                WHERE symbol = ? AND date >= ? AND date <= ?
                ORDER BY date ASC
            """
            df = pd.read_sql_query(query, conn, params=(symbol, start_date, end_date))
        else:
            query = """
                SELECT date, open, high, low, close, volume
                FROM daily_prices
                WHERE symbol = ?
                ORDER BY date DESC
                LIMIT ?
            """
            df = pd.read_sql_query(query, conn, params=(symbol, days))

        conn.close()

        if df.empty:
            return df

        df['date'] = pd.to_datetime(df['date'])
        df = df.sort_values('date').reset_index(drop=True)
        return df

    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """計算技術指標"""
        if df.empty or len(df) < 20:
            return df

        # 移動平均線
        df['MA5'] = df['close'].rolling(window=5).mean()
        df['MA10'] = df['close'].rolling(window=10).mean()
        df['MA20'] = df['close'].rolling(window=20).mean()
        df['MA60'] = df['close'].rolling(window=60).mean()

        # RSI (14日)
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        df['RSI'] = 100 - (100 / (1 + rs))

        # MACD
        exp1 = df['close'].ewm(span=12, adjust=False).mean()
        exp2 = df['close'].ewm(span=26, adjust=False).mean()
        df['MACD'] = exp1 - exp2
        df['MACD_Signal'] = df['MACD'].ewm(span=9, adjust=False).mean()
        df['MACD_Hist'] = df['MACD'] - df['MACD_Signal']

        # 布林通道
        df['BB_Mid'] = df['close'].rolling(window=20).mean()
        df['BB_Std'] = df['close'].rolling(window=20).std()
        df['BB_Upper'] = df['BB_Mid'] + 2 * df['BB_Std']
        df['BB_Lower'] = df['BB_Mid'] - 2 * df['BB_Std']
        df['BB_Width'] = (df['BB_Upper'] - df['BB_Lower']) / df['BB_Mid']
        df['BB_Position'] = (df['close'] - df['BB_Lower']) / (df['BB_Upper'] - df['BB_Lower'])

        # 成交量指標
        df['Volume_MA20'] = df['volume'].rolling(window=20).mean()
        df['Volume_Ratio'] = df['volume'] / df['Volume_MA20']

        # ATR (Average True Range)
        high_low = df['high'] - df['low']
        high_close = np.abs(df['high'] - df['close'].shift())
        low_close = np.abs(df['low'] - df['close'].shift())
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        df['ATR'] = tr.rolling(window=14).mean()

        # 動量指標
        df['ROC'] = df['close'].pct_change(periods=10) * 100  # 10日變動率

        return df

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        """生成交易信號"""
        if df.empty or len(df) < 60:
            return df

        df = self.calculate_indicators(df)

        # 初始化信號欄位
        df['Signal_MA'] = 0  # 均線策略
        df['Signal_RSI'] = 0  # RSI策略
        df['Signal_MACD'] = 0  # MACD策略
        df['Signal_BB'] = 0  # 布林通道策略

        # 1. 均線交叉策略 (MA5 > MA20 = 買入, MA5 < MA20 = 賣出)
        df.loc[df['MA5'] > df['MA20'], 'Signal_MA'] = 1
        df.loc[df['MA5'] < df['MA20'], 'Signal_MA'] = -1

        # 2. RSI 策略 (RSI < 30 超賣買入, RSI > 70 超買賣出)
        df.loc[df['RSI'] < 30, 'Signal_RSI'] = 1
        df.loc[df['RSI'] > 70, 'Signal_RSI'] = -1

        # 3. MACD 策略 (MACD > Signal = 買入)
        df.loc[df['MACD'] > df['MACD_Signal'], 'Signal_MACD'] = 1
        df.loc[df['MACD'] < df['MACD_Signal'], 'Signal_MACD'] = -1

        # 4. 布林通道策略 (價格觸及下軌買入, 觸及上軌賣出)
        df.loc[df['close'] < df['BB_Lower'], 'Signal_BB'] = 1
        df.loc[df['close'] > df['BB_Upper'], 'Signal_BB'] = -1

        # 綜合信號 (加權平均)
        df['Signal_Combined'] = (
            df['Signal_MA'] * 0.3 +
            df['Signal_RSI'] * 0.2 +
            df['Signal_MACD'] * 0.3 +
            df['Signal_BB'] * 0.2
        )

        return df

    def backtest_strategy(self, df: pd.DataFrame, strategy: str = 'MA') -> Dict:
        """回測策略"""
        if df.empty or len(df) < 60:
            return {
                'total_return': 0,
                'win_rate': 0,
                'total_trades': 0,
                'sharpe_ratio': 0,
                'max_drawdown': 0,
                'buy_hold_return': 0
            }

        df = self.generate_signals(df.copy())

        signal_col = f'Signal_{strategy}'
        if signal_col not in df.columns:
            signal_col = 'Signal_MA'

        # 計算策略報酬
        df['Position'] = df[signal_col].shift(1)  # 前一天的信號決定今天的持倉
        df['Strategy_Return'] = df['Position'] * df['close'].pct_change()
        df['Cumulative_Return'] = (1 + df['Strategy_Return']).cumprod()

        # 計算買入持有報酬
        df['Buy_Hold_Return'] = df['close'].pct_change()
        df['Buy_Hold_Cumulative'] = (1 + df['Buy_Hold_Return']).cumprod()

        # 統計交易次數
        df['Trade'] = df['Position'].diff().abs()
        total_trades = df['Trade'].sum() / 2  # 買賣各算一次

        # 計算勝率
        winning_days = (df['Strategy_Return'] > 0).sum()
        total_trading_days = (df['Position'] != 0).sum()
        win_rate = winning_days / total_trading_days if total_trading_days > 0 else 0

        # 計算夏普比率 (假設無風險利率為0)
        strategy_returns = df['Strategy_Return'].dropna()
        sharpe_ratio = 0
        if len(strategy_returns) > 0 and strategy_returns.std() > 0:
            sharpe_ratio = (strategy_returns.mean() / strategy_returns.std()) * np.sqrt(252)

        # 計算最大回撤
        cumulative = df['Cumulative_Return'].dropna()
        if len(cumulative) > 0:
            rolling_max = cumulative.expanding().max()
            drawdown = (cumulative - rolling_max) / rolling_max
            max_drawdown = drawdown.min()
        else:
            max_drawdown = 0

        # 總報酬
        total_return = df['Cumulative_Return'].iloc[-1] - 1 if len(df['Cumulative_Return'].dropna()) > 0 else 0
        buy_hold_return = df['Buy_Hold_Cumulative'].iloc[-1] - 1 if len(df['Buy_Hold_Cumulative'].dropna()) > 0 else 0

        return {
            'total_return': total_return * 100,
            'win_rate': win_rate * 100,
            'total_trades': int(total_trades),
            'sharpe_ratio': sharpe_ratio,
            'max_drawdown': max_drawdown * 100,
            'buy_hold_return': buy_hold_return * 100
        }

    def get_current_analysis(self, symbol: str) -> Dict:
        """取得當前分析結果和建議"""
        df = self.get_price_data(symbol, days=365)

        if df.empty or len(df) < 60:
            return {
                'symbol': symbol,
                'recommendation': 'HOLD',
                'confidence': 0,
                'signals': {},
                'indicators': {},
                'backtest': {},
                'reason': '數據不足，無法分析'
            }

        df = self.generate_signals(df)
        latest = df.iloc[-1]

        # 取得各策略信號
        signals = {
            'MA': int(latest['Signal_MA']),
            'RSI': int(latest['Signal_RSI']),
            'MACD': int(latest['Signal_MACD']),
            'BB': int(latest['Signal_BB']),
            'Combined': float(latest['Signal_Combined'])
        }

        # 取得指標數值
        indicators = {
            'price': float(latest['close']),
            'MA5': float(latest['MA5']) if pd.notna(latest['MA5']) else None,
            'MA20': float(latest['MA20']) if pd.notna(latest['MA20']) else None,
            'MA60': float(latest['MA60']) if pd.notna(latest['MA60']) else None,
            'RSI': float(latest['RSI']) if pd.notna(latest['RSI']) else None,
            'MACD': float(latest['MACD']) if pd.notna(latest['MACD']) else None,
            'MACD_Signal': float(latest['MACD_Signal']) if pd.notna(latest['MACD_Signal']) else None,
            'BB_Upper': float(latest['BB_Upper']) if pd.notna(latest['BB_Upper']) else None,
            'BB_Lower': float(latest['BB_Lower']) if pd.notna(latest['BB_Lower']) else None,
            'BB_Position': float(latest['BB_Position']) if pd.notna(latest['BB_Position']) else None,
            'Volume_Ratio': float(latest['Volume_Ratio']) if pd.notna(latest['Volume_Ratio']) else None,
            'ATR': float(latest['ATR']) if pd.notna(latest['ATR']) else None,
        }

        # 回測各策略
        backtest_results = {}
        for strategy in ['MA', 'RSI', 'MACD', 'BB']:
            backtest_results[strategy] = self.backtest_strategy(df.copy(), strategy)

        # 綜合建議
        combined_signal = signals['Combined']
        confidence = abs(combined_signal) * 100

        # 生成建議理由
        reasons = []

        if signals['MA'] == 1:
            reasons.append("短期均線在長期均線之上，趨勢向上")
        elif signals['MA'] == -1:
            reasons.append("短期均線在長期均線之下，趨勢向下")

        if indicators['RSI'] is not None:
            if indicators['RSI'] < 30:
                reasons.append(f"RSI={indicators['RSI']:.1f}，處於超賣區")
            elif indicators['RSI'] > 70:
                reasons.append(f"RSI={indicators['RSI']:.1f}，處於超買區")

        if signals['MACD'] == 1:
            reasons.append("MACD 金叉，動能轉強")
        elif signals['MACD'] == -1:
            reasons.append("MACD 死叉，動能轉弱")

        if indicators['BB_Position'] is not None:
            if indicators['BB_Position'] < 0.2:
                reasons.append("價格接近布林下軌，可能超賣")
            elif indicators['BB_Position'] > 0.8:
                reasons.append("價格接近布林上軌，可能超買")

        # 決定建議
        if combined_signal >= 0.4:
            recommendation = 'STRONG_BUY'
            recommendation_text = '強力買進'
        elif combined_signal >= 0.15:
            recommendation = 'BUY'
            recommendation_text = '買進'
        elif combined_signal <= -0.4:
            recommendation = 'STRONG_SELL'
            recommendation_text = '強力賣出'
        elif combined_signal <= -0.15:
            recommendation = 'SELL'
            recommendation_text = '賣出'
        else:
            recommendation = 'HOLD'
            recommendation_text = '持有觀望'

        # 加入回測績效作為參考
        best_strategy = max(backtest_results.items(), key=lambda x: x[1]['total_return'])
        if best_strategy[1]['total_return'] > 0:
            reasons.append(f"回測最佳策略: {best_strategy[0]}，年報酬 {best_strategy[1]['total_return']:.1f}%")

        return {
            'symbol': symbol,
            'recommendation': recommendation,
            'recommendation_text': recommendation_text,
            'confidence': confidence,
            'combined_signal': combined_signal,
            'signals': signals,
            'indicators': indicators,
            'backtest': backtest_results,
            'reasons': reasons,
            'date': latest['date'].strftime('%Y-%m-%d') if pd.notna(latest['date']) else None
        }

    def get_all_recommendations(self) -> List[Dict]:
        """取得所有股票的建議"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT symbol FROM watchlist WHERE is_active = 1")
        symbols = [row[0] for row in cursor.fetchall()]
        conn.close()

        results = []
        for symbol in symbols:
            try:
                analysis = self.get_current_analysis(symbol)
                results.append(analysis)
            except Exception as e:
                print(f"Error analyzing {symbol}: {e}")

        return results

    def get_top_picks(self, n: int = 10) -> Tuple[List[Dict], List[Dict]]:
        """取得最佳買進和賣出標的"""
        all_analysis = self.get_all_recommendations()

        # 過濾有效分析
        valid = [a for a in all_analysis if a['confidence'] > 0]

        # 排序
        sorted_by_signal = sorted(valid, key=lambda x: x['combined_signal'], reverse=True)

        # 買進標的 (信號 > 0)
        buy_picks = [a for a in sorted_by_signal if a['combined_signal'] > 0][:n]

        # 賣出標的 (信號 < 0)
        sell_picks = [a for a in sorted_by_signal if a['combined_signal'] < 0][-n:]
        sell_picks.reverse()

        return buy_picks, sell_picks

    def get_trade_history(self, symbol: str, strategy: str = 'MA',
                          initial_capital: float = 100000,
                          start_date: str = None, end_date: str = None) -> Dict:
        """
        取得詳細的交易歷史記錄

        Args:
            symbol: 股票代碼
            strategy: 策略名稱 (MA/RSI/MACD/BB)
            initial_capital: 初始資金
            start_date: 開始日期 (YYYY-MM-DD)
            end_date: 結束日期 (YYYY-MM-DD)

        Returns:
            包含交易記錄、資金曲線等詳細資訊
        """
        df = self.get_price_data(symbol, days=365, start_date=start_date, end_date=end_date)

        if df.empty or len(df) < 60:
            return {
                'trades': [],
                'equity_curve': [],
                'summary': {},
                'df': pd.DataFrame()
            }

        df = self.generate_signals(df)
        signal_col = f'Signal_{strategy}'

        if signal_col not in df.columns:
            signal_col = 'Signal_MA'

        # 找出買賣點
        trades = []
        position = 0  # 0=空手, 1=持有
        entry_price = 0
        entry_date = None
        shares = 0
        capital = initial_capital
        equity_curve = []

        for i, row in df.iterrows():
            current_signal = row[signal_col]
            current_price = row['close']
            current_date = row['date']

            # 記錄每日權益
            if position == 1:
                current_equity = capital + shares * current_price
            else:
                current_equity = capital

            equity_curve.append({
                'date': current_date,
                'equity': current_equity,
                'position': position
            })

            # 買入信號 (空手時)
            if current_signal == 1 and position == 0:
                shares = int(capital / current_price)
                if shares > 0:
                    entry_price = current_price
                    entry_date = current_date
                    capital = capital - shares * current_price
                    position = 1

            # 賣出信號 (持有時)
            elif current_signal == -1 and position == 1:
                exit_price = current_price
                exit_date = current_date
                profit = (exit_price - entry_price) * shares
                profit_pct = (exit_price - entry_price) / entry_price * 100
                capital = capital + shares * exit_price

                trades.append({
                    'entry_date': entry_date.strftime('%Y-%m-%d') if hasattr(entry_date, 'strftime') else str(entry_date),
                    'entry_price': entry_price,
                    'exit_date': exit_date.strftime('%Y-%m-%d') if hasattr(exit_date, 'strftime') else str(exit_date),
                    'exit_price': exit_price,
                    'shares': shares,
                    'profit': profit,
                    'profit_pct': profit_pct,
                    'capital_after': capital
                })

                position = 0
                shares = 0

        # 如果還持有，計算未實現損益
        if position == 1:
            last_price = df.iloc[-1]['close']
            last_date = df.iloc[-1]['date']
            unrealized_profit = (last_price - entry_price) * shares
            unrealized_pct = (last_price - entry_price) / entry_price * 100

            trades.append({
                'entry_date': entry_date.strftime('%Y-%m-%d') if hasattr(entry_date, 'strftime') else str(entry_date),
                'entry_price': entry_price,
                'exit_date': '持有中',
                'exit_price': last_price,
                'shares': shares,
                'profit': unrealized_profit,
                'profit_pct': unrealized_pct,
                'capital_after': capital + shares * last_price,
                'is_open': True
            })

        # 計算總結
        if trades:
            closed_trades = [t for t in trades if not t.get('is_open', False)]
            winning_trades = [t for t in closed_trades if t['profit'] > 0]
            losing_trades = [t for t in closed_trades if t['profit'] <= 0]

            total_profit = sum(t['profit'] for t in trades)
            final_equity = equity_curve[-1]['equity'] if equity_curve else initial_capital

            summary = {
                'initial_capital': initial_capital,
                'final_equity': final_equity,
                'total_profit': total_profit,
                'total_return_pct': (final_equity - initial_capital) / initial_capital * 100,
                'total_trades': len(trades),
                'winning_trades': len(winning_trades),
                'losing_trades': len(losing_trades),
                'win_rate': len(winning_trades) / len(closed_trades) * 100 if closed_trades else 0,
                'avg_profit': np.mean([t['profit'] for t in closed_trades]) if closed_trades else 0,
                'avg_profit_pct': np.mean([t['profit_pct'] for t in closed_trades]) if closed_trades else 0,
                'max_profit': max([t['profit'] for t in trades]) if trades else 0,
                'max_loss': min([t['profit'] for t in trades]) if trades else 0,
                'buy_hold_return': (df.iloc[-1]['close'] - df.iloc[0]['close']) / df.iloc[0]['close'] * 100
            }
        else:
            summary = {
                'initial_capital': initial_capital,
                'final_equity': initial_capital,
                'total_profit': 0,
                'total_return_pct': 0,
                'total_trades': 0,
                'winning_trades': 0,
                'losing_trades': 0,
                'win_rate': 0,
                'avg_profit': 0,
                'avg_profit_pct': 0,
                'max_profit': 0,
                'max_loss': 0,
                'buy_hold_return': (df.iloc[-1]['close'] - df.iloc[0]['close']) / df.iloc[0]['close'] * 100 if len(df) > 0 else 0
            }

        # 在 df 中標記買賣點
        df['trade_signal'] = 0
        for trade in trades:
            entry_mask = df['date'].astype(str).str[:10] == trade['entry_date']
            df.loc[entry_mask, 'trade_signal'] = 1  # 買入

            if trade.get('exit_date') != '持有中':
                exit_mask = df['date'].astype(str).str[:10] == trade['exit_date']
                df.loc[exit_mask, 'trade_signal'] = -1  # 賣出

        return {
            'trades': trades,
            'equity_curve': equity_curve,
            'summary': summary,
            'df': df
        }
