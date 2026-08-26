import pandas as pd
from sklearn.preprocessing import MinMaxScaler
import matplotlib.pyplot as plt
import joblib
import matplotlib.dates as mdates
import numpy as np

class Preprocessor:
    def __init__(self, df=None, features=None, target=None):
        self.df = df
        self.features = features
        self.target = target

        self.x_scaler = MinMaxScaler()
        self.y_scaler = MinMaxScaler()

    # RSI 지수를 계산
    def calculate_rsi(self, period = 14):
        delta = self.df['Close'].diff()

        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)

        avg_gain = gain.rolling(period).mean()
        avg_loss = loss.rolling(period).mean()

        rs = avg_gain / avg_loss

        return 100 - (100 / (1 + rs))

    # 피쳐를 추가
    def create_features(self):
        self.df['Date'] = pd.to_datetime(self.df['Date'])
        self.df = self.df.sort_values('Date').reset_index(drop=True)

        self.df['Diff'] = self.df['Close'].pct_change()                 # 주가 변화율
        self.df['MA5'] = self.df['Close'].rolling(window=5).mean()      # MA5
        self.df['MA20'] = self.df['Close'].rolling(window=20).mean()    # MA20
        self.df['RSI'] = self.calculate_rsi()                           # RSI

        self.df = self.df.dropna().reset_index(drop=True)   # 결측치 제거

        return self.df

    # 피쳐와 타겟을 분리
    def split_features_target(self, df):
        X = df[self.features]
        y = df[self.target]

        return X, y

    # 학습, 검증, 테스트 데이터를 나눔
    def train_val_test_division(self, train_ratio=0.8, val_ratio=0.1):
        train_index = int(len(self.df) * train_ratio)
        val_index = int(len(self.df) * (train_ratio+val_ratio))

        train = self.df.iloc[:train_index]
        val = self.df.iloc[train_index:val_index]
        test = self.df.iloc[val_index:]

        return train, val, test
    
    # 스케일러로 변환, 학습
    def fit_transform(self, x_data, y_data):
        X = self.x_scaler.fit_transform(x_data)
        y = self.y_scaler.fit_transform(y_data)

        return X, y
    
    # 스케일러로 변환
    def transform(self, x_data, y_data = None):
        X = self.x_scaler.transform(x_data)

        if y_data is not None:
            y = self.y_scaler.transform(y_data)
        else :
            y = None

        return X, y
    
    # 스케일러로 역변환
    def inverse_transform(self, y_data):
        return self.y_scaler.inverse_transform(y_data)
    
    # 시계열 데이터를 생성
    def create_sequence(self, X, y, window_size=20):
        X_seq = []
        y_seq = []

        for i in range(len(X) - window_size):
            X_seq.append(X[i:i+window_size])
            y_seq.append(y[i+window_size])

        return np.array(X_seq), np.array(y_seq)
    
    # 학습된 스케일러를 파일 형태로 저장
    def save_scaler(self):
        scaler_model_location = "..//models//"
        scaler_model_name = "stock_price_scaler"
        scaler_model_ext = "gz"

        joblib.dump(self.scaler, scaler_model_location + scaler_model_name + "." + scaler_model_ext)

    