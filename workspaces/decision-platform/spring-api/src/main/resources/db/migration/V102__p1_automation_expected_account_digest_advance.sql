-- 체결이 확정된 다음 세션이 항상 ACCOUNT_DRIFT로 HALT했다.
--
-- V94는 계보를 전진시키면서 expected_account_projection_v2만 갱신하고
-- expected_account_digest_v2는 arm 시점 값 그대로 두었다. 그런데 엔진이 드리프트를 판정할 때
-- 보는 값(`accountDigestMatches`)은 투영이 아니라 그 다이제스트다. 즉 기대치의 두 축 가운데
-- 실제로 읽히는 쪽이 첫 체결 이후로 멈춰 있었고, 브로커 잔고가 자기 체결만큼 움직이는 순간
-- 불일치가 되어 다음 세션이 HALT했다. HALT는 stop으로 풀리지 않는다.
--
-- 두 축을 같이 민다. 다이제스트는 p1_automation_risk_balance_projection_v2가 만드는 모양을
-- 그대로 재구성해 해시한다. 그 함수는 cashKrw와 quantity를 문자열로, schemaVersion을 숫자로
-- 담고 jsonb의 ::text를 해시하므로, 모양이 한 글자라도 다르면 영원히 일치하지 않는다.
--
-- 체결가에 수수료가 실제와 다르게 붙으면 기대와 관측이 갈리고 드리프트가 정상적으로 발동한다.
-- 이 변경은 그 감시를 끄지 않는다. 자기 체결로 설명되는 이동만 기대치에 반영할 뿐이다.

CREATE OR REPLACE FUNCTION public.p1_advance_automation_account_lineage_v3(
  p_run_id text,p_claim_token_hash text,p_reason text,p_next_projection jsonb,
  p_next_digest text,p_order_id text,p_filled_quantity bigint,p_average_fill_price_krw bigint
)
RETURNS integer
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog
AS $p1_advance_automation_account_lineage_v3$
DECLARE claim_row public.automation_runtime_claim%ROWTYPE;
DECLARE control_row public.automation_control%ROWTYPE;
DECLARE existing_row public.automation_account_lineage%ROWTYPE;
DECLARE run_row public.automation_runs%ROWTYPE;
DECLARE next_risk_projection jsonb;
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
  WHERE run_id=p_run_id AND claim_token_hash=p_claim_token_hash FOR UPDATE;
  IF NOT FOUND THEN
    PERFORM set_config('app.automation_claim_scan','0',true);
    RAISE EXCEPTION 'automation claim unavailable' USING ERRCODE='42501';
  END IF;
  PERFORM set_config('app.automation_claim_scan','0',true);
  PERFORM set_config('app.automation_owner_user_id',claim_row.user_id,true);
  -- 반납된 claim은 자기 run의 종결 직후에만, 그리고 durable state가 그 체결을 증언할 때만
  -- 받아들인다. 시계나 유예창이 아니라 기록으로 판정한다.
  IF claim_row.claim_state<>'ACTIVE' THEN
    SELECT * INTO run_row FROM public.automation_runs WHERE run_id=p_run_id;
    IF NOT FOUND OR run_row.user_id<>claim_row.user_id OR run_row.state<>'COMPLETED' THEN
      RAISE EXCEPTION 'automation claim unavailable' USING ERRCODE='42501';
    END IF;
    PERFORM 1 FROM public.automation_order_reservations
    WHERE run_id=p_run_id AND user_id=claim_row.user_id AND order_id=p_order_id
      AND filled_quantity=p_filled_quantity AND average_fill_price_krw=p_average_fill_price_krw;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'automation claim unavailable' USING ERRCODE='42501';
    END IF;
  END IF;
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
  -- 기대 투영과 기대 다이제스트를 함께 전진시킨다. 두 축은 같은 계좌 상태를 가리키지만
  -- 표현이 다르다. accountDigestMatches는 p1_automation_risk_balance_projection_v2가 만드는
  -- 모양(cashKrw와 quantity가 문자열, schemaVersion이 숫자)을 ::text로 해시하므로, 여기서도
  -- 같은 모양으로 다시 만들어 해시한다. 원본 p_next_projection을 그대로 해시하면 숫자와
  -- 문자열이 어긋나 영원히 일치하지 않는다.
  SELECT jsonb_build_object(
    'accountId',p_next_projection->>'accountId',
    'cashKrw',p_next_projection->>'cashKrw',
    'schemaVersion',2,
    'positions',COALESCE((
      SELECT jsonb_agg(jsonb_build_object(
        'quantity',item->>'quantity','symbol',item->>'symbol'
      ) ORDER BY item->>'symbol')
      FROM jsonb_array_elements(p_next_projection->'positions') item
    ),'[]'::jsonb)
  ) INTO next_risk_projection;
  UPDATE public.automation_control
  SET expected_account_projection_v2=p_next_projection,
      expected_account_digest_v2=encode(public.digest(
        convert_to(next_risk_projection::text,'UTF8'),'sha256'),'hex'),
      updated_at=statement_timestamp()
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
