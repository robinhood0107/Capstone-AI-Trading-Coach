-- 새 KIS 모의계좌를 연결해도 옛 계좌 scope 의 마지막 관측이 ACTIVE 로 남아, 소유자 잔고 문맥이
-- 두 행이 되었다. Spring 은 두 행을 CONFLICT 로 보고 RiskEngine 이 모든 매수를 HOLD 했다
-- (PORTFOLIO_CONTEXT_UNAVAILABLE, 2026-09-27 계좌 연결 이후). 옛 scope 를 은퇴시키는 관측은
-- 어디서도 쓰이지 않는다. 소유자·소스마다 가장 최근에 관측된 scope 하나만 문맥으로 본다 -
-- 연결된 계좌는 주문 직전마다 새로 관측되므로 항상 현재 계좌가 남는다.
CREATE OR REPLACE VIEW public.latest_portfolio_balance_observations WITH (security_barrier=true) AS
 WITH latest_balance AS (
         SELECT DISTINCT ON (candidate.owner_user_id, candidate.account_scope_hash) candidate.observation_id,
            candidate.owner_user_id,
            candidate.account_scope_hash,
            candidate.source,
            candidate.context_status,
            candidate.cash_krw,
            candidate.portfolio_equity_krw,
            candidate.margin_requirement_krw,
            candidate.completeness,
            candidate.position_count,
            candidate.observed_at,
            candidate.received_at,
            candidate.schema_version,
            candidate.source_version,
            candidate.payload_json,
            candidate.source_ref,
            candidate.artifact_hash,
            candidate.created_at
           FROM portfolio_balance_observations candidate
          WHERE (candidate.owner_user_id = current_setting('app.actor_user_id'::text, true))
          ORDER BY candidate.owner_user_id, candidate.account_scope_hash, candidate.observed_at DESC, candidate.received_at DESC, candidate.observation_id
        )
 SELECT observation_id,
    owner_user_id,
    account_scope_hash,
    source,
    cash_krw,
    portfolio_equity_krw,
    margin_requirement_krw,
    completeness,
    position_count,
    observed_at,
    received_at,
    schema_version,
    source_version,
    source_ref,
    artifact_hash,
    COALESCE(( SELECT jsonb_agg(jsonb_build_object('symbol', "position".symbol, 'quantity', "position".quantity, 'marketValueKrw', "position".market_value_krw, 'isGoldEtfEtn', "position".is_gold_etf_etn) ORDER BY "position".symbol) AS jsonb_agg
           FROM portfolio_position_observations "position"
          WHERE ("position".balance_observation_id = balance.observation_id)), '[]'::jsonb) AS positions_json
   FROM latest_balance balance
  WHERE (context_status = 'ACTIVE'::text)
    AND observation_id = (SELECT peer.observation_id FROM latest_balance peer
      WHERE peer.owner_user_id = balance.owner_user_id AND peer.source = balance.source
        AND peer.context_status = 'ACTIVE'::text
      ORDER BY peer.observed_at DESC, peer.received_at DESC, peer.observation_id DESC LIMIT 1);
