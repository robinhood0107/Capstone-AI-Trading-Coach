from collections import defaultdict
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

class BacktestEngine:
    def __init__(self, model, initial_cash = 10_000_000):
        self.model = model                  # 모델 구조
        self.df = None                      # 시그널 데이터
        self.initial_cash = initial_cash    # 초기 자산
        self.cash = initial_cash            # 현재 자산
        self.shares = 0                     # 보유 주식 수
        self.order_log = []                 # 주문 기록
        self.daily_assets = []              # 매일 총 자산 기록
        self.final_asset = 0                # 최종 자산

    # 시그널 데이터를 기반으로 매수, 매도를 진행한다
    def run(self, df):
        self.df = df
        for _, row in self.df.iterrows():
            date = row['Date']
            price = row['Close']
            signal = row['Signal']

            if signal == 'BUY' or signal == 'SELL':
                self.process_signal(date, price, signal)
            
            current_portfolio = self.calculate_portfolio(date, price)
            self.daily_assets.append(current_portfolio)

        self.final_asset = self.daily_assets[-1][1]

    # price 가격의 주식을 shares 만큼 매수/매도하는 시그널을 수행
    def process_signal(self, date, price, signal):        
        if signal == 'BUY':
            shares = self.cash // price
            self.update_position(date, price, shares, 1.0)
        elif signal == 'SELL':
            shares = self.shares
            self.update_position(date, price, shares, -1.0)

    # 매수, 매도를 진행
    def update_position(self, date, price, shares, direction):
        if shares == 0:
            return
        cost = price * shares * direction
        self.cash -= cost
        self.shares += (shares * direction)
        self.write_log(date, direction, price, shares)
    
    # 매수, 매도 주문을 기록하는 log 작성
    def write_log(self, date, direction, price, shares):
        self.order_log.append({
            "date": date,
            "type": "BUY" if direction == 1 else "SELL", 
            "price": price, 
            "shares": shares
        })

    # 현재 보유한 주식의 포트폴리오를 계산
    def calculate_portfolio(self, date, price):
        asset = self.cash + self.shares * price
        return (date, asset)

    # 최종 수익률 평가
    def get_performance(self):
        profit = (self.final_asset - self.initial_cash) / self.initial_cash
        mmd = self.calculate_mmd()
        calmar = self.calculate_calmar(profit, mmd)
        sharpe = self.calculate_sharpe()
        win_rate = self.calculate_win_rate()
        max_peak = max(asset for _, asset in self.daily_assets)

        return {
            "profit": profit,
            "mmd": mmd,
            "calmar": calmar,
            "sharpe": sharpe,
            "win rate": win_rate,
            "max peak": max_peak,
            "final asset": self.final_asset,
            "trade count": len(self.order_log)
        }
    
    # MMD 지수 계산
    def calculate_mmd(self):
        df = pd.DataFrame(self.daily_assets, columns=['Date', 'Asset'])
        peak = df['Asset'].cummax()
        drawdown = (df['Asset'] - peak) / peak
        return drawdown.min()
    
    # 칼마 지수
    def calculate_calmar(self, profit, mmd):
        if mmd == 0:
            return 0.0
        return profit / abs(mmd)
    
    # 샤프 지수
    def calculate_sharpe(self, risk_free_rate=0):
        df = pd.DataFrame(self.daily_assets, columns=['Date', 'Asset'])

        returns = df['Asset'].pct_change().dropna()

        # 일 단위 무위험수익률
        daily_rf = risk_free_rate / 252

        if len(returns) < 2 or returns.std() == 0 :
            sharpe = 0.0
        else :
            sharpe = (returns.mean() - daily_rf) / returns.std()
            sharpe *= (252 ** 0.5)

        return sharpe

    # 매수-매도 거래 기록 작성
    def make_trades(self):
        trades = []
        buy = None

        for order in self.order_log:
            if order['type'] == 'BUY':
                buy = order
            elif order['type'] == 'SELL' and buy:
                profit = (order['price'] - buy['price']) * buy['shares']
                trades.append({
                    'buy':buy,
                    'sell':order,
                    'profit':profit
                })

                buy = None
        
        return trades
    
    # 승률 계산
    def calculate_win_rate(self):
        trades = self.make_trades()
        if not trades: 
            return 0
        
        wins = sum(1 for t in trades if t['profit'] > 0)

        return wins / len(trades)

    # 로그를 데이터프레임으로 변환하여 반환
    def get_trade_log_df(self):
        return pd.DataFrame(self.order_log)
    
    # 매일 총 자산 관리 기록을 그래프로 출력
    def plot_daily_assets(self):
        df = pd.DataFrame(self.daily_assets, columns=["Date", "Asset"])

        plt.figure(figsize=(12, 6))

        plt.plot(df["Date"], df["Asset"], label="Portfolio Value")

        plt.title("Portfolio Value")
        plt.xlabel("Date")
        plt.ylabel("Asset")

        plt.grid(True)
        plt.legend()

        plt.tight_layout()
        plt.show()
