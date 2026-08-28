-- expected_account_projection_v2는 arm에서 한 번 기록되고 그 뒤로 갱신하는 경로가 없었다.
-- 봇이 체결하면 실제 KIS 잔고가 그 스냅샷에서 벗어나므로 다음 tick이 ACCOUNT_DRIFT로
-- HALTED가 되고, p1_stop_automation_runtime_v1은 HALTED를 풀어주지 않는다. 즉 첫 체결이
-- 자동운용을 영구히 멈췄다.
--
-- 확인된 자기 체결에 한해 기대 투영을 전진시키고, 그 이동을 automation_account_lineage에
-- 남긴다. lineage 테이블과 BUY_FILL/SELL_FILL 사유는 V91이 이미 만들어 두었지만 쓰는 곳이
-- 없었다. 델타가 자기 체결로 설명되는지는 엔진(AccountLineageSnapshot.permits_fill)이 판정하고,
-- 여기서는 구조와 소유권만 강제한다.

CREATE FUNCTION public.p1_advance_automation_account_lineage_v3(
  p_run_id text,p_claim_token_hash text,p_reason text,p_next_projection jsonb,
  p_next_digest text,p_order_id text,p_filled_quantity bigint,p_average_fill_price_krw bigint
)
RETURNS integer
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog
AS $p1_advance_automation_account_lineage_v3$
DECLARE claim_row public.automation_runtime_claim%ROWTYPE;
DECLARE control_row public.automation_control%ROWTYPE;
DECLARE existing_row public.automation_account_lineage%ROWTYPE;
DECLARE prior_digest_value text;
DECLARE sequence_value integer;
BEGIN
  IF session_user<>'decision_automation_runtime'
     OR p_run_id!~'^auto_run_[0-9a-f]{32}$'
     OR p_claim_token_hash!~'^sha256:[0-9a-f]{64}$'
     OR p_reason NOT IN ('BUY_FILL','SELL_FILL')
     OR p_next_digest!~'^[0-9a-f]{64}$'
     OR p_order_id!~'^ord_mock_[0-9a-f]{32}$'
     OR p_filled_quantity<=0 OR p_average_fill_price_krw<=0
     OR NOT public.p1_automation_structural_projection_valid_v2(p_next_projection) THEN
    RAISE EXCEPTION 'automation account lineage input invalid' USING ERRCODE='22023';
  END IF;
  PERFORM set_config('app.automation_claim_scan','1',true);
  SELECT * INTO claim_row FROM public.automation_runtime_claim
  WHERE run_id=p_run_id AND claim_token_hash=p_claim_token_hash AND claim_state='ACTIVE' FOR UPDATE;
  IF NOT FOUND THEN
    PERFORM set_config('app.automation_claim_scan','0',true);
    RAISE EXCEPTION 'automation claim unavailable' USING ERRCODE='42501';
  END IF;
  PERFORM set_config('app.automation_claim_scan','0',true);
  PERFORM set_config('app.automation_owner_user_id',claim_row.user_id,true);
  SELECT * INTO control_row FROM public.automation_control
  WHERE user_id=claim_row.user_id FOR UPDATE;
  IF NOT FOUND OR control_row.account_id<>p_next_projection->>'accountId' THEN
    RAISE EXCEPTION 'automation account lineage scope denied' USING ERRCODE='42501';
  END IF;
  -- tick 재시도가 lineage를 두 번 밀지 않게 한다. 같은 run과 주문이면 순수 no-op이다.
  SELECT * INTO existing_row FROM public.automation_account_lineage
  WHERE user_id=claim_row.user_id AND run_id=p_run_id AND order_id=p_order_id AND reason=p_reason;
  IF FOUND THEN
    IF existing_row.next_digest<>p_next_digest THEN
      RAISE EXCEPTION 'automation account lineage conflict' USING ERRCODE='23505';
    END IF;
    RETURN existing_row.sequence;
  END IF;
  SELECT next_digest INTO prior_digest_value FROM public.automation_account_lineage
  WHERE user_id=claim_row.user_id ORDER BY sequence DESC LIMIT 1;
  IF prior_digest_value IS NULL THEN
    RAISE EXCEPTION 'automation account lineage baseline missing' USING ERRCODE='40001';
  END IF;
  SELECT COALESCE(max(sequence),0)+1 INTO sequence_value
  FROM public.automation_account_lineage WHERE user_id=claim_row.user_id;
  INSERT INTO public.automation_account_lineage(
    lineage_id,user_id,run_id,sequence,reason,prior_digest,next_digest,order_id,
    filled_quantity,average_fill_price_krw,occurred_at
  ) VALUES (
    'auto_acl_'||substr(encode(public.digest(convert_to(
      claim_row.user_id||':'||sequence_value::text||':'||p_reason,'UTF8'),'sha256'),'hex'),1,32),
    claim_row.user_id,p_run_id,sequence_value,p_reason,prior_digest_value,p_next_digest,p_order_id,
    p_filled_quantity,p_average_fill_price_krw,statement_timestamp()
  );
  -- 기대 투영만 전진시킨다. expected_account_digest_v2는 portfolio_balance_observations를
  -- 따라가는 별개 축이므로 여기서 건드리면 SQL 쪽 accountDigestMatches가 도리어 깨진다.
  UPDATE public.automation_control
  SET expected_account_projection_v2=p_next_projection,updated_at=statement_timestamp()
  WHERE user_id=claim_row.user_id;
  RETURN sequence_value;
END
$p1_advance_automation_account_lineage_v3$;

ALTER FUNCTION public.p1_advance_automation_account_lineage_v3(
  text,text,text,jsonb,text,text,bigint,bigint
) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.p1_advance_automation_account_lineage_v3(
  text,text,text,jsonb,text,text,bigint,bigint
) FROM PUBLIC,decision_app,decision_worker,decision_replay,decision_replay_authorizer;
GRANT EXECUTE ON FUNCTION public.p1_advance_automation_account_lineage_v3(
  text,text,text,jsonb,text,text,bigint,bigint
) TO decision_automation_runtime;
