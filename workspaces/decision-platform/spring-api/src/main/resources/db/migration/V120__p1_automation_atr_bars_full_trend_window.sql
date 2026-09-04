-- p1_read_automation_atr_bars_v1 의 조회 상한을 101 -> 400 으로 올린다.
--
-- 왜
--
-- RULE_BASELINE 이 종목의 전체 동향을 보게 하려면 장기 이동평균이 필요하다. 문헌의 추세
-- 정의가 그만큼 길다 - Brock, Lakonishok & LeBaron (1992, JoF) 의 VMA 규칙은 1/150 · 1/200 을
-- 포함하고 Faber (2007) 는 10개월(약 200세션) SMA 대비 가격 상태로 보유를 정한다.
--
-- 그런데 이 함수가 p_limit 을 1..101 로 묶어 두었다. 이 상한이 정해질 때 소비자는 ATR(period
-- 14)과 MA20 뿐이어서 101 이 넉넉했지만, 지금은 시장데이터가 매일 쌓이고(이미 103세션) 장기
-- 추세를 보려면 그보다 많이 읽어야 한다. 즉 계약이 요구하는 값도 안전 경계도 아닌 부수적인
-- 상한이 정상 경로를 막고 있다.
--
-- 무엇이 바뀌지 않는가
--
-- SECURITY DEFINER, session_user='decision_automation_runtime' 검사, exact-31 유니버스 검사,
-- session_date < p_as_of_session 경계, 정렬, 반환 컬럼이 모두 그대로다. 권한은 넓어지지 않고
-- 같은 행을 더 많이 읽을 수 있게만 된다. 400 은 200세션 MA 에 워밍업과 여유를 더한 값이다.
--
-- 짧은 이력은 그대로 통과한다
--
-- 호출자가 400 을 요청해도 그 종목에 있는 만큼만 돌아온다. 상장 이력이 짧은 종목이
-- 이 변경으로 배제되거나 실패하지 않는다 - 26년 PIT 패널에서 31종목 전부가 신호를 내는 것을
-- 확인했고, 2021~2022 상장 종목도 워밍업 22세션만 잃는다.

CREATE OR REPLACE FUNCTION public.p1_read_automation_atr_bars_v1(
  p_symbol text,
  p_as_of_session date,
  p_limit integer
) RETURNS TABLE(
  symbol text,
  session_date date,
  open_price bigint,
  high_price bigint,
  low_price bigint,
  close_price bigint,
  volume bigint,
  temporal_quality text,
  source_receipt_sha256 text
)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path=pg_catalog,public
AS $p1_read_automation_atr_bars_v1$
BEGIN
  IF session_user<>'decision_automation_runtime'
     OR p_symbol!~'^[0-9]{6}$'
     OR p_as_of_session IS NULL
     OR p_limit NOT BETWEEN 1 AND 400 THEN
    RAISE EXCEPTION 'automation ATR history request is invalid' USING ERRCODE='42501';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM public.market_data_operational_universe AS universe
    WHERE universe.symbol=p_symbol
  ) THEN
    RAISE EXCEPTION 'automation ATR symbol is outside current exact-31' USING ERRCODE='55000';
  END IF;

  RETURN QUERY
  SELECT bounded.symbol, bounded.session_date, bounded.open_price, bounded.high_price,
         bounded.low_price, bounded.close_price, bounded.volume,
         bounded.temporal_quality, bounded.source_receipt_sha256
  FROM (
    SELECT bars.symbol, bars.session_date, bars.open_price, bars.high_price,
           bars.low_price, bars.close_price, bars.volume,
           bars.temporal_quality, bars.source_receipt_sha256
    FROM public.market_data_operational_bars AS bars
    WHERE bars.symbol=p_symbol AND bars.session_date<p_as_of_session
    ORDER BY bars.session_date DESC
    LIMIT p_limit
  ) AS bounded
  ORDER BY bounded.session_date;
END
$p1_read_automation_atr_bars_v1$;

ALTER FUNCTION public.p1_read_automation_atr_bars_v1(text,date,integer) OWNER TO flyway;

REVOKE ALL ON FUNCTION public.p1_read_automation_atr_bars_v1(text,date,integer)
  FROM PUBLIC;

GRANT EXECUTE ON FUNCTION public.p1_read_automation_atr_bars_v1(text,date,integer)
  TO decision_automation_runtime;
