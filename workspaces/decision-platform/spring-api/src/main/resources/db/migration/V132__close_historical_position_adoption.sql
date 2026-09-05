REVOKE ALL ON FUNCTION public.p1_adopt_historical_mock_position_v1(
  text,text,bigint,date,bigint,date,text
) FROM PUBLIC,decision_automation_runtime;

DROP FUNCTION public.p1_adopt_historical_mock_position_v1(
  text,text,bigint,date,bigint,date,text
);
