-- 지속형 automation 런타임의 체크포인트 전진이 실제 PostgreSQL에서 한 번도 성공한 적이 없다.
-- `p1_advance_automation_checkpoint_v2`는 `RETURNS TABLE(checkpoint_version integer, ...)`로
-- 선언돼 `checkpoint_version`이 OUT 변수인데, 체크포인트를 갱신하는 UPDATE의 WHERE 절이 같은
-- 이름을 컬럼으로도 참조한다. PL/pgSQL은 이것을 42702 `column reference is ambiguous`로 거절한다.
--
--   WHERE run_id=p_run_id AND checkpoint_version=p_expected_version
--
-- 즉 매 tick의 CAS가 실패해 run이 SCHEDULED에서 한 걸음도 나아가지 못한다. V91이 이 함수를
-- 만들 때부터 있던 문제이고 V93이 그대로 옮겼다. `automation_runtime_checkpoint`와
-- `automation_processed_ticks`가 지금까지 0행인 것이 그 증거다 — 단위 테스트는 가짜 저장소를
-- 쓰고, 지금까지의 live 검증은 durable 경로가 아니라 in-memory 엔진과 brokerage를 직접 태웠다.
--
-- 고치는 것은 그 한 줄뿐이다. 나머지 본문은 V93과 같다. 참조를 테이블로 한정해 OUT 변수와
-- 겹치지 않게 한다. `#variable_conflict use_column`을 쓰지 않은 이유는 그 지시어가 함수 전체의
-- 다른 참조 해석까지 한꺼번에 바꾸기 때문이다.

CREATE OR REPLACE FUNCTION public.p1_advance_automation_checkpoint_v2(
  p_run_id text,p_claim_token_hash text,p_tick_identity_hash text,p_expected_version integer,
  p_next_state text,p_selected_symbol text,p_selected_side text,p_decision_id text,
  p_vertex_call_count integer,p_provider_call_count integer,p_logical_submit_count integer,
  p_reservation_id text,p_quantity bigint,p_limit_price_krw bigint,p_exact_intent_json text,
  p_exact_intent_sha256 text,p_quote_snapshot_json text,p_policy_id text,p_policy_version integer,
  p_position_expiry_session date,p_filled_quantity bigint,p_leaves_quantity bigint,
  p_unfilled_terminated_quantity bigint,p_average_fill_price_krw bigint,p_exit_reason text,
  p_expected_account_digest text,p_order_id text,p_provider_order_ref_hash text,p_result_hash text,
  p_event_type text,p_event_payload_hash text
)
RETURNS TABLE(checkpoint_version integer,replayed boolean)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog
AS $p1_advance_automation_checkpoint_v2$
DECLARE claim_row public.automation_runtime_claim%ROWTYPE;
DECLARE checkpoint_row public.automation_runtime_checkpoint%ROWTYPE;
DECLARE prior_tick public.automation_processed_ticks%ROWTYPE;
DECLARE reservation_row public.automation_order_reservations%ROWTYPE;
DECLARE control_row public.automation_control%ROWTYPE;
DECLARE policy_row public.automation_policy_versions%ROWTYPE;
DECLARE decision_row public.decisions%ROWTYPE;
DECLARE artifact_row public.decision_artifacts%ROWTYPE;
DECLARE position_row public.automation_positions%ROWTYPE;
DECLARE next_version integer;
DECLARE sequence_value integer;
DECLARE event_seed text;
DECLARE terminal boolean;
DECLARE estimated_amount bigint;
DECLARE remaining_quantity bigint;
DECLARE exit_quantity_total bigint;
DECLARE exit_price_weighted bigint;
DECLARE realized_delta bigint;
BEGIN
  IF session_user<>'decision_automation_runtime' OR p_run_id!~'^auto_run_[0-9a-f]{32}$'
     OR p_claim_token_hash!~'^sha256:[0-9a-f]{64}$'
     OR p_tick_identity_hash!~'^sha256:[0-9a-f]{64}$'
     OR p_result_hash!~'^sha256:[0-9a-f]{64}$' OR p_event_payload_hash!~'^[0-9a-f]{64}$'
     OR p_expected_version<1 OR p_vertex_call_count NOT BETWEEN 0 AND 1
     OR p_provider_call_count NOT BETWEEN 0 AND 16 OR p_logical_submit_count NOT BETWEEN 0 AND 1
     OR p_filled_quantity<0 OR p_leaves_quantity<0 OR p_unfilled_terminated_quantity<0
     OR (p_average_fill_price_krw IS NULL)<>(p_filled_quantity=0)
     OR (p_exit_reason IS NOT NULL AND p_exit_reason NOT IN (
       'STOP_LOSS','MAX_HOLDING_SESSIONS','MODEL_SELL','TAKE_PROFIT'
     ))
     OR p_event_type NOT IN ('CONTROL_CHANGED','RUN_TRANSITIONED','BASELINE_CAPTURED','ACCOUNT_RECONCILED',
       'EXIT_SELECTED','BUY_SELECTED','NEWS_RESULT_RECORDED','RISK_RESULT_RECORDED','ORDER_RESERVED',
       'ORDER_OUTCOME_RECORDED','CANCEL_RECORDED','DRIFT_DETECTED','RUN_HALTED') THEN
    RAISE EXCEPTION 'automation v2 checkpoint input invalid' USING ERRCODE='22023';
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
  SELECT * INTO prior_tick FROM public.automation_processed_ticks
  WHERE run_id=p_run_id AND tick_identity_hash=p_tick_identity_hash;
  IF FOUND THEN
    IF prior_tick.result_hash<>p_result_hash THEN
      RAISE EXCEPTION 'automation tick identity conflict' USING ERRCODE='23505';
    END IF;
    checkpoint_version:=prior_tick.checkpoint_version;replayed:=true;RETURN NEXT;RETURN;
  END IF;
  SELECT * INTO checkpoint_row FROM public.automation_runtime_checkpoint WHERE run_id=p_run_id FOR UPDATE;
  SELECT * INTO control_row FROM public.automation_control WHERE user_id=claim_row.user_id FOR UPDATE;
  IF checkpoint_row.run_id IS NULL OR checkpoint_row.checkpoint_version<>p_expected_version
     OR NOT public.p1_automation_transition_valid_v2(checkpoint_row.state,p_next_state)
     OR p_vertex_call_count<checkpoint_row.vertex_call_count
     OR p_provider_call_count<checkpoint_row.provider_call_count
     OR p_logical_submit_count<checkpoint_row.logical_submit_count
     OR control_row.policy_id IS NULL OR control_row.policy_id IS DISTINCT FROM p_policy_id
     OR control_row.policy_version IS DISTINCT FROM p_policy_version
     OR control_row.expected_account_digest_v2 IS DISTINCT FROM p_expected_account_digest THEN
    RAISE EXCEPTION 'automation v2 checkpoint CAS conflict' USING ERRCODE='40001';
  END IF;
  IF control_row.control_state='HALTED' OR (
    control_row.control_state='DISARMED' AND checkpoint_row.state NOT IN (
      'RECONCILING_PREVIOUS','ORDER_SUBMITTED','PENDING_RECONCILIATION'
    )
  ) THEN RAISE EXCEPTION 'automation control disallows new work' USING ERRCODE='40001'; END IF;
  IF p_quote_snapshot_json IS NOT NULL THEN
    IF octet_length(p_quote_snapshot_json) NOT BETWEEN 2 AND 4096
       OR jsonb_typeof(p_quote_snapshot_json::jsonb)<>'object'
       OR p_quote_snapshot_json::jsonb->>'symbol' IS DISTINCT FROM p_selected_symbol THEN
      RAISE EXCEPTION 'automation quote snapshot invalid' USING ERRCODE='22023';
    END IF;
    IF checkpoint_row.quote_snapshot_json IS NOT NULL
       AND checkpoint_row.quote_snapshot_json::jsonb<>p_quote_snapshot_json::jsonb THEN
      RAISE EXCEPTION 'automation quote snapshot drift' USING ERRCODE='40001';
    END IF;
  END IF;
  IF p_reservation_id IS NOT NULL THEN
    IF p_reservation_id!~'^auto_res_[0-9a-f]{32}$' OR p_quantity<=0 OR p_limit_price_krw<=0
       OR p_limit_price_krw>9223372036854775807/p_quantity
       OR p_exact_intent_json IS NULL OR octet_length(p_exact_intent_json) NOT BETWEEN 2 AND 4096
       OR jsonb_typeof(p_exact_intent_json::jsonb)<>'object'
       OR (SELECT count(*) FROM jsonb_object_keys(p_exact_intent_json::jsonb))<>8
       OR NOT p_exact_intent_json::jsonb ?& ARRAY[
         'estimatedAmount','estimatedPrice','orderType','quantity','side','strategyId','symbol','timeframe'
       ]
       OR p_exact_intent_json::jsonb->>'symbol' IS DISTINCT FROM p_selected_symbol
       OR p_exact_intent_json::jsonb->>'side' IS DISTINCT FROM p_selected_side
       OR p_exact_intent_json::jsonb->>'orderType'<>'LIMIT'
       OR (p_exact_intent_json::jsonb->>'quantity')::bigint<>p_quantity
       OR (p_exact_intent_json::jsonb->>'estimatedPrice')::bigint<>p_limit_price_krw
       OR p_exact_intent_json::jsonb->>'strategyId'<>control_row.strategy_id
       OR p_exact_intent_json::jsonb->>'timeframe'<>'1d'
       OR p_exact_intent_sha256!~'^[0-9a-f]{64}$'
       OR encode(public.digest(convert_to(p_exact_intent_json,'UTF8'),'sha256'),'hex')<>p_exact_intent_sha256
       OR p_quote_snapshot_json IS NULL THEN
      RAISE EXCEPTION 'automation exact order intent invalid' USING ERRCODE='22023';
    END IF;
    estimated_amount:=(p_exact_intent_json::jsonb->>'estimatedAmount')::bigint;
    IF estimated_amount<>p_quantity*p_limit_price_krw
       OR p_filled_quantity+p_leaves_quantity+p_unfilled_terminated_quantity<>p_quantity THEN
      RAISE EXCEPTION 'automation order quantity conservation failed' USING ERRCODE='40001';
    END IF;
    SELECT * INTO reservation_row FROM public.automation_order_reservations WHERE run_id=p_run_id FOR UPDATE;
    IF FOUND THEN
      IF reservation_row.reservation_id<>p_reservation_id OR reservation_row.symbol<>p_selected_symbol
         OR reservation_row.side<>p_selected_side OR reservation_row.quantity<>p_quantity
         OR reservation_row.limit_price_krw<>p_limit_price_krw
         OR reservation_row.estimated_amount_krw<>estimated_amount
         OR reservation_row.order_intent_sha256<>p_exact_intent_sha256
         OR reservation_row.exact_intent_json::jsonb<>p_exact_intent_json::jsonb
         OR p_filled_quantity<reservation_row.filled_quantity
         OR p_unfilled_terminated_quantity<reservation_row.unfilled_terminated_quantity THEN
        RAISE EXCEPTION 'automation reservation drift' USING ERRCODE='40001';
      END IF;
      UPDATE public.automation_order_reservations SET
        logical_submit_count=p_logical_submit_count,
        order_id=COALESCE(automation_order_reservations.order_id,p_order_id),
        provider_order_ref_hash=COALESCE(automation_order_reservations.provider_order_ref_hash,p_provider_order_ref_hash),
        filled_quantity=p_filled_quantity,leaves_quantity=p_leaves_quantity,
        unfilled_terminated_quantity=p_unfilled_terminated_quantity,
        average_fill_price_krw=p_average_fill_price_krw,
        reconciliation_status=CASE
          WHEN p_next_state IN ('COMPLETED','CANCELLED_UNFILLED') THEN 'MATCHED'
          ELSE automation_order_reservations.reconciliation_status END,
        exit_reason=COALESCE(automation_order_reservations.exit_reason,p_exit_reason),
        updated_at=statement_timestamp()
      WHERE run_id=p_run_id;
    ELSE
      IF checkpoint_row.state<>'ORDER_SIZING' OR p_next_state<>'RISK_CHECKING' THEN
        RAISE EXCEPTION 'automation reservation must precede risk' USING ERRCODE='40001';
      END IF;
      INSERT INTO public.automation_order_reservations(
        reservation_id,run_id,user_id,session_date,symbol,side,quantity,limit_price_krw,
        logical_submit_count,order_id,provider_order_ref_hash,created_at,updated_at,
        estimated_amount_krw,strategy_id,policy_id,policy_version,principle_version_id,
        exact_intent_json,quote_snapshot_json,order_intent_sha256,filled_quantity,leaves_quantity,
        unfilled_terminated_quantity,average_fill_price_krw,reconciliation_status,exit_reason
      ) VALUES (
        p_reservation_id,p_run_id,claim_row.user_id,claim_row.session_date,p_selected_symbol,
        p_selected_side,p_quantity,p_limit_price_krw,p_logical_submit_count,p_order_id,
        p_provider_order_ref_hash,statement_timestamp(),statement_timestamp(),estimated_amount,
        control_row.strategy_id,control_row.policy_id,control_row.policy_version,
        control_row.principle_version_id,p_exact_intent_json,p_quote_snapshot_json,
        p_exact_intent_sha256,p_filled_quantity,p_leaves_quantity,p_unfilled_terminated_quantity,
        p_average_fill_price_krw,'NOT_APPLICABLE',p_exit_reason
      );
    END IF;
    IF p_decision_id IS NOT NULL THEN
      SELECT * INTO decision_row FROM public.decisions
      WHERE decision_id=p_decision_id AND user_id=claim_row.user_id;
      SELECT * INTO artifact_row FROM public.decision_artifacts WHERE decision_id=p_decision_id;
      IF decision_row.decision_id IS NULL OR decision_row.outcome<>'ALLOW'
         OR NOT decision_row.can_submit_order OR decision_row.enforcement_action<>'NONE'
         OR decision_row.portfolio_source<>'KIS_MOCK'
         OR decision_row.principle_version_id<>control_row.principle_version_id
         OR decision_row.valid_until<=statement_timestamp() OR artifact_row.decision_id IS NULL
         OR EXISTS (
           SELECT 1 FROM jsonb_object_keys(p_exact_intent_json::jsonb) key
           WHERE artifact_row.snapshot_artifact_canonical_json::jsonb->'orderIntent'->>key
             IS DISTINCT FROM p_exact_intent_json::jsonb->>key
         ) THEN
        RAISE EXCEPTION 'automation exact Decision intent mismatch' USING ERRCODE='40001';
      END IF;
    END IF;
  ELSIF p_quantity IS NOT NULL OR p_limit_price_krw IS NOT NULL OR p_exact_intent_json IS NOT NULL
     OR p_exact_intent_sha256 IS NOT NULL OR p_order_id IS NOT NULL OR p_provider_order_ref_hash IS NOT NULL
     OR p_filled_quantity<>0 OR p_leaves_quantity<>0 OR p_unfilled_terminated_quantity<>0
     OR p_average_fill_price_krw IS NOT NULL THEN
    RAISE EXCEPTION 'automation order state lacks reservation' USING ERRCODE='22023';
  END IF;
  IF p_next_state='EXIT_SELECTED' AND p_selected_side='SELL' THEN
    UPDATE public.automation_positions SET status='EXIT_PENDING',exit_reason=p_exit_reason
    WHERE user_id=claim_row.user_id AND symbol=p_selected_symbol AND status='OPEN';
    IF NOT FOUND THEN RAISE EXCEPTION 'automation SELL lot unavailable' USING ERRCODE='40001'; END IF;
  END IF;
  IF p_next_state='COMPLETED' AND p_reservation_id IS NOT NULL AND p_filled_quantity>0 THEN
    SELECT * INTO policy_row FROM public.automation_policy_versions
    WHERE policy_id=control_row.policy_id AND version=control_row.policy_version;
    IF p_selected_side='BUY' THEN
      IF p_position_expiry_session IS NULL OR p_position_expiry_session<=claim_row.session_date
         OR p_order_id IS NULL THEN
        RAISE EXCEPTION 'automation completed BUY evidence invalid' USING ERRCODE='40001';
      END IF;
      INSERT INTO public.automation_positions(
        position_id,user_id,account_id,symbol,quantity,entry_session,expiry_session,status,
        bot_owned,short_allowed,created_at,closed_at,entry_order_id,entry_ordered_quantity,
        entry_filled_quantity,entry_unfilled_quantity,entry_average_fill_price_krw,
        policy_id,policy_version,stop_loss_bps,take_profit_bps,exit_filled_quantity,
        exit_average_fill_price_krw,exit_reason
      ) VALUES (
        'auto_pos_'||substr(encode(public.digest(convert_to(
          p_run_id||':'||p_selected_symbol||':'||claim_row.session_date::text,'UTF8'),'sha256'),'hex'),1,32),
        claim_row.user_id,control_row.account_id,p_selected_symbol,p_filled_quantity,
        claim_row.session_date,p_position_expiry_session,'OPEN',true,false,statement_timestamp(),NULL,
        p_order_id,p_quantity,p_filled_quantity,p_unfilled_terminated_quantity,p_average_fill_price_krw,
        control_row.policy_id,control_row.policy_version,policy_row.stop_loss_bps,
        policy_row.take_profit_bps,0,NULL,NULL
      );
    ELSE
      SELECT * INTO position_row FROM public.automation_positions
      WHERE user_id=claim_row.user_id AND account_id=control_row.account_id
        AND symbol=p_selected_symbol AND status IN ('OPEN','EXIT_PENDING') FOR UPDATE;
      IF NOT FOUND OR position_row.quantity<p_filled_quantity THEN
        RAISE EXCEPTION 'automation completed SELL lot unavailable' USING ERRCODE='40001';
      END IF;
      remaining_quantity:=position_row.quantity-p_filled_quantity;
      -- 부분 청산이 이어져도 엔진(app/p1_owner/automation.py::_apply_fill)과 같은 값이 되도록
      -- 수량가중 평균으로 누적하고, 왕복 비용 35bp를 올림 적용한 실현손익을 함께 적는다.
      exit_quantity_total:=position_row.exit_filled_quantity+p_filled_quantity;
      exit_price_weighted:=(
        position_row.exit_filled_quantity*COALESCE(position_row.exit_average_fill_price_krw,0)
        + p_filled_quantity*p_average_fill_price_krw
      )/exit_quantity_total;
      realized_delta:=NULL;
      IF position_row.entry_average_fill_price_krw IS NOT NULL THEN
        realized_delta:=
          (p_average_fill_price_krw-position_row.entry_average_fill_price_krw)*p_filled_quantity
          - (
            (p_average_fill_price_krw+position_row.entry_average_fill_price_krw)
              *p_filled_quantity*35+19999
          )/20000;
      END IF;
      UPDATE public.automation_positions SET
        quantity=remaining_quantity,exit_filled_quantity=exit_quantity_total,
        exit_average_fill_price_krw=exit_price_weighted,exit_reason=p_exit_reason,
        realized_pnl_krw=CASE
          WHEN realized_delta IS NULL THEN realized_pnl_krw
          ELSE COALESCE(realized_pnl_krw,0)+realized_delta
        END,
        status=CASE WHEN remaining_quantity=0 THEN 'CLOSED' ELSE 'OPEN' END,
        closed_at=CASE WHEN remaining_quantity=0 THEN statement_timestamp() ELSE NULL END
      WHERE position_id=position_row.position_id;
    END IF;
  ELSIF p_selected_side='SELL' AND p_next_state IN (
    'NEWS_VETOED','CANCELLED_UNFILLED','SKIPPED_NO_ACTION','SKIPPED_DATA_UNAVAILABLE',
    'SKIPPED_LATE_START','HALTED'
  ) THEN
    UPDATE public.automation_positions SET status='OPEN'
    WHERE user_id=claim_row.user_id AND symbol=p_selected_symbol AND status='EXIT_PENDING';
  END IF;
  next_version:=checkpoint_row.checkpoint_version+1;
  UPDATE public.automation_runtime_checkpoint SET
    checkpoint_version=next_version,state=p_next_state,selected_symbol=p_selected_symbol,
    selected_side=p_selected_side,decision_id=COALESCE(automation_runtime_checkpoint.decision_id,p_decision_id),
    vertex_call_count=p_vertex_call_count,provider_call_count=p_provider_call_count,
    logical_submit_count=p_logical_submit_count,
    quote_snapshot_json=COALESCE(automation_runtime_checkpoint.quote_snapshot_json,p_quote_snapshot_json),
    exit_reason=COALESCE(automation_runtime_checkpoint.exit_reason,p_exit_reason),updated_at=statement_timestamp()
  WHERE automation_runtime_checkpoint.run_id=p_run_id
    AND automation_runtime_checkpoint.checkpoint_version=p_expected_version;
  UPDATE public.automation_runs SET
    state=p_next_state,selected_symbol=p_selected_symbol,selected_side=p_selected_side,
    physical_submit_count=p_logical_submit_count,vertex_call_count=p_vertex_call_count,
    provider_calls=p_provider_call_count,policy_id=control_row.policy_id,
    policy_version=control_row.policy_version,
    expected_account_digest_before=COALESCE(expected_account_digest_before,control_row.expected_account_digest_v2),
    updated_at=statement_timestamp() WHERE run_id=p_run_id;
  SELECT COALESCE(max(sequence),0)+1 INTO sequence_value FROM public.automation_events WHERE run_id=p_run_id;
  event_seed:=p_run_id||':'||sequence_value::text||':'||p_event_type||':'||p_event_payload_hash;
  INSERT INTO public.automation_events(
    event_id,run_id,user_id,sequence,event_type,occurred_at,payload_hash,provider_calls,order_submits,sanitized
  ) VALUES (
    'auto_evt_'||substr(encode(public.digest(convert_to(event_seed,'UTF8'),'sha256'),'hex'),1,32),
    p_run_id,claim_row.user_id,sequence_value,p_event_type,statement_timestamp(),p_event_payload_hash,
    p_provider_call_count,p_logical_submit_count,true
  );
  INSERT INTO public.automation_processed_ticks(
    run_id,tick_identity_hash,result_hash,checkpoint_version,processed_at
  ) VALUES (p_run_id,p_tick_identity_hash,p_result_hash,next_version,statement_timestamp());
  terminal:=p_next_state IN ('NEWS_VETOED','CANCELLED_UNFILLED','COMPLETED',
    'SKIPPED_NO_ACTION','SKIPPED_DATA_UNAVAILABLE','SKIPPED_LATE_START','HALTED');
  IF p_next_state='HALTED' AND control_row.control_state<>'HALTED' THEN
    UPDATE public.automation_control SET control_state='HALTED',version=version+1,
      updated_at=statement_timestamp() WHERE user_id=claim_row.user_id;
  END IF;
  IF terminal THEN
    UPDATE public.automation_runtime_claim SET claim_state='RELEASED',released_at=statement_timestamp()
    WHERE run_id=p_run_id;
    UPDATE public.automation_runtime_schedule SET
      schedule_state=CASE WHEN p_next_state='HALTED' THEN 'HALTED' ELSE 'COMPLETED' END,
      updated_at=statement_timestamp() WHERE user_id=claim_row.user_id AND session_date=claim_row.session_date;
  END IF;
  checkpoint_version:=next_version;replayed:=false;RETURN NEXT;
END
$p1_advance_automation_checkpoint_v2$;
