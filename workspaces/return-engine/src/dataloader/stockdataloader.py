class StockDataLoader:
    @staticmethod
    def download(stock_code, start, end, path):
        # Provider 의존성은 명시적 refresh 경로에서만 lazy import한다.
        # Compose preview는 이 메서드를 호출하지 않는다.
        import yfinance as yf

        df = yf.download(
            stock_code,
            start=start,
            end=end,
            auto_adjust=False
        )

        df.columns = df.columns.get_level_values(0)
        df.to_csv(path)

        return df
