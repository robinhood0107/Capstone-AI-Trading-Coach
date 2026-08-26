import pandas as pd

class SignalGenerator:
    BUY = 'BUY'
    HOLD = 'HOLD'
    SELL = 'SELL'

    # 규칙 Baseline 전용 신호 생성기
    @staticmethod
    def from_baseline(df):
        result = df[['Date', 'Close']].copy()
        ma5 = df['MA5']
        ma20 = df['MA20']
        rsi = df['RSI']
        
        # 이전 값과 비교하기 위해 shift() 사용
        golden_cross = (ma5 > ma20) & (ma5.shift(1) <= ma20.shift(1))
        dead_cross = (ma5 < ma20) & (ma5.shift(1) >= ma20.shift(1))
        
        buy_cond = golden_cross & (rsi < 70)
        sell_cond = dead_cross & (rsi > 30)
    
        result['Signal'] = SignalGenerator.HOLD
        result.loc[buy_cond, 'Signal'] = SignalGenerator.BUY
        result.loc[sell_cond, 'Signal'] = SignalGenerator.SELL

        return result[['Date', 'Close', 'Signal']]
        
    # LSTM 모델 전용 신호 생성기
    @staticmethod
    def from_prediction(df, buy_threshold=0.001, sell_threshold=0.001):
        result = df.copy()

        pred_change = result['Prediction'].shift(1).pct_change()

        result['Signal'] = SignalGenerator.HOLD
        result.loc[pred_change > buy_threshold, 'Signal'] = SignalGenerator.BUY
        result.loc[pred_change < -sell_threshold, 'Signal'] = SignalGenerator.SELL

        return result[['Date', 'Close', 'Signal']]