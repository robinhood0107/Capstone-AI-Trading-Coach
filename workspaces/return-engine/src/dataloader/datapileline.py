import torch
from torch.utils.data import Dataset, DataLoader

class StockDataset(Dataset):
    def __init__(self, X, y):
        self.X = torch.FloatTensor(X)
        self.y = torch.FloatTensor(y)

    def __len__(self):
        return len(self.X)
    
    def __getitem__(self, index):
        return self.X[index], self.y[index]

class DataPipeline:
    def __init__(self, preprocessor, window_size=20, batch_size=32):
        self.preprocessor = preprocessor
        self.window_size = window_size
        self.batch_size = batch_size

    def create_dataloader(self, X, y, fit=False):
        if fit:     # fit_transform
            X_scaled, y_scaled = self.preprocessor.fit_transform(X, y)
        else:               # transform
            X_scaled, y_scaled = self.preprocessor.transform(X, y)

        X_seq, y_seq = self.preprocessor.create_sequence(X_scaled, y_scaled, self.window_size)

        dataset = StockDataset(X_seq, y_seq)
        data_loader = DataLoader(dataset, batch_size=self.batch_size, shuffle=False)

        return data_loader
