--시장 freshness 판정은 휴장일/거래일 기준이 필요하므로 최소 seed만 스키마 마이그레이션에 포함한다.
CREATE TABLE market_calendar (
  market text NOT NULL,
  calendar_date date NOT NULL,
  is_trading_day boolean NOT NULL,
  holiday_name text,
  source text NOT NULL DEFAULT 'S0.4_FIXTURE',
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (market, calendar_date)
);

--2026-06-23은 API 예시 거래일 fixture이고 전체 KRX 공식 달력 수집은 S0.4 범위 밖이다.
INSERT INTO market_calendar (market, calendar_date, is_trading_day, holiday_name)
VALUES ('KRX', DATE '2026-06-23', true, NULL);

--2026-01-01은 휴장일 fixture로 stale/freshness 테스트의 기준점을 제공한다.
INSERT INTO market_calendar (market, calendar_date, is_trading_day, holiday_name)
VALUES ('KRX', DATE '2026-01-01', false, 'New Year');
