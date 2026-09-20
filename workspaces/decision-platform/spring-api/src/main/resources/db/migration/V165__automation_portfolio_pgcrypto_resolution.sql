-- pgcrypto digest는 public schema에 설치된다. PUBLIC CREATE가 철회된 고정 schema만 추가한다.
ALTER FUNCTION public.p1_record_automation_buyable_receipt_v1(text,text,jsonb)
  SET search_path TO pg_catalog,public;
ALTER FUNCTION public.p1_stage_automation_portfolio_plan_v2(text,text,jsonb,jsonb)
  SET search_path TO pg_catalog,public;
ALTER FUNCTION public.p1_finish_automation_portfolio_execution_v2(
  text,text,integer,text,text,text,bigint,bigint,bigint,text
) SET search_path TO pg_catalog,public;
ALTER FUNCTION public.p1_adopt_automation_position_v1(text,text,text,text,date,boolean)
  SET search_path TO pg_catalog,public;
