from matplotlib import pyplot as plt
import pandas as pd
import torch
import torch.nn as nn
import numpy as np

class StockLSTM(nn.Module):
    def __init__(self, input_size, hidden_size=64, num_layers=2, output_size=1, dropout=0.2):
        super().__init__()

        self.lstm = nn.LSTM(input_size=input_size,
                            hidden_size=hidden_size,
                            num_layers=num_layers,
                            dropout=dropout,
                            batch_first=True)
        
        self.fc = nn.Linear(hidden_size, output_size)

    def forward(self, x):
        out, _ = self.lstm(x)
        out = out[:,-1,:]
        out = self.fc(out)

        return out
    

class LSTMModel():
    def __init__(self, input_size, hidden_size=64, num_layers=2, learning_rate=0.001, dropout=0.2):
        self.device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
        self.model = StockLSTM(input_size, hidden_size, num_layers, dropout=dropout).to(self.device)
        self.optimizer = torch.optim.Adam(self.model.parameters(), lr=learning_rate)
        self.criterion = nn.SmoothL1Loss()

    # Epochs만큼 모델의 학습을 진행
    def train(self, train_loader, val_loader, epochs=50):
        self.model.train()

        train_losses = []
        val_losses = []

        for epoch in range(epochs):
            total_loss = 0

            for X, y in train_loader:
                X = X.to(self.device)
                y = y.to(self.device)

                self.optimizer.zero_grad()

                prediction = self.model(X)
                loss = self.criterion(prediction, y)
                loss.backward()

                self.optimizer.step()
                total_loss += loss.item()

            train_loss = total_loss / len(train_loader)
            val_loss = self.validate(val_loader)
            
            train_losses.append(train_loss)
            val_losses.append(val_loss)

            if ((epoch+1) % 5 == 0) :
                print(f'[{epoch+1}/{epochs}] Loss:{train_loss:.5f}, Val_loss:{val_loss:.5f}', flush=True)

    # 검증용 데이터를 통해 Loss를 평가
    def validate(self, val_loader):
        self.model.eval()

        total_loss = 0
        
        with torch.no_grad():
            for X, y in val_loader:
                X = X.to(self.device)
                y = y.to(self.device)

                prediction = self.model(X)
                loss = self.criterion(prediction, y)
                
                total_loss += loss.item()
        
        return total_loss / len(val_loader)

    # 데이터를 통해 예측
    def predict(self, df, preprocessor, data_pipeline):
        self.model.eval()

        predictions = []

        X_data, y_data = preprocessor.split_features_target(df)
        loader = data_pipeline.create_dataloader(X_data, y_data)


        with torch.no_grad():
            for X, y in loader:
                X = X.to(self.device)

                prediction = self.model(X)
                predictions.extend(prediction.cpu().numpy())
        
        predictions = np.array(predictions).reshape(-1,1)
        predictions = preprocessor.inverse_transform(predictions)

        window_size = data_pipeline.window_size
        result = df.iloc[window_size:].copy().reset_index(drop=True)
        if len(predictions) != len(result):
            raise ValueError(
                "prediction count must match the window-aligned result row count"
            )
        result["Prediction"] = predictions

        return result

    # 최근 20일 데이터를 기반으로 다음날 가격 예측
    def forecast(self, df, preprocessor, data_pipeline):
        self.model.eval()

        window_size = data_pipeline.window_size

        recent_df = df.tail(window_size)

        X, _ = preprocessor.split_features_target(recent_df)
        X, _ = preprocessor.transform(X)      

        X = torch.FloatTensor(X).unsqueeze(0).to(self.device)
        # (20, feature) -> (1, 20, feature)

        with torch.no_grad():
            prediction = self.model(X)

        prediction = prediction.cpu().numpy().reshape(-1, 1)
        prediction = preprocessor.inverse_transform(prediction)

        return prediction[0][0]
        
    # 모델을 파일로 저장
    def save(self, path):
        torch.save(self.model.state_dict(), path)

    # 모델 불러오기
    def load(self, path):
        self.model.load_state_dict(
            torch.load(path, map_location=self.device, weights_only=True)
        )
        self.model.eval()

    # df에 다음날 예측 레코드 생성 및 변화율 저장
    def make_predict_record(self, df, pred_df, preprocessor, data_pipeline, next_session):
        prediction = self.forecast(df, preprocessor, data_pipeline)

        new_row = pred_df.iloc[-1].copy()
        new_row['Date'] = pd.Timestamp(next_session)
        new_row['Prediction'] = prediction

        new_row['Open'] = np.nan
        new_row['High'] = np.nan
        new_row['Low'] = np.nan
        new_row['Close'] = np.nan
        new_row['Volume'] = np.nan
        new_row['MA5'] = np.nan
        new_row['MA20'] = np.nan
        new_row['RSI'] = np.nan

        pred_df = pd.concat([pred_df, pd.DataFrame([new_row])], ignore_index=True)
        pred_df['Change'] = pred_df['Prediction'].pct_change(fill_method=None)

        pred_df['ActualChange'] = pred_df['Close'].pct_change(fill_method=None)
        
        return pred_df
