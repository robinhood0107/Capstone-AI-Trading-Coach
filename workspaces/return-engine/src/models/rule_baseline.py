import pandas as pd
import matplotlib.pyplot as plt
from backtest_core.signal_generator import SignalGenerator

class BaselineModel:
    def __init__(self, symbol=None):
        self.df = None
        self.symbol = symbol        # 종목명

    def predict(self, data):
        return SignalGenerator.from_baseline(data)
        