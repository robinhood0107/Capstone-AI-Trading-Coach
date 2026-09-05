CREATE TABLE public.instrument_display_metadata (
  symbol text PRIMARY KEY CHECK (symbol~'^[0-9]{6}$'),
  name_ko text NOT NULL CHECK (char_length(name_ko) BETWEEN 1 AND 64),
  logo_text text NOT NULL CHECK (char_length(logo_text) BETWEEN 1 AND 8),
  brand_color text NOT NULL CHECK (brand_color~'^#[0-9A-F]{6}$'),
  market text NOT NULL CHECK (market IN ('KOSPI','ETF')),
  display_order integer NOT NULL UNIQUE CHECK (display_order BETWEEN 1 AND 31)
);

INSERT INTO public.instrument_display_metadata(symbol,name_ko,logo_text,brand_color,market,display_order) VALUES
('005930','삼성전자','삼성','#1428A0','KOSPI',1),('000660','SK하이닉스','SK','#EA002C','KOSPI',2),
('005935','삼성전자우','삼성','#1428A0','KOSPI',3),('402340','SK스퀘어','SK','#EA002C','KOSPI',4),
('009150','삼성전기','삼성','#1428A0','KOSPI',5),('373220','LG에너지솔루션','LG','#A50034','KOSPI',6),
('005380','현대자동차','현대','#002C5F','KOSPI',7),('207940','삼성바이오로직스','삼성','#1428A0','KOSPI',8),
('032830','삼성생명','삼성','#1428A0','KOSPI',9),('105560','KB금융','KB','#FFBC00','KOSPI',10),
('028260','삼성물산','삼성','#1428A0','KOSPI',11),('012450','한화에어로스페이스','한화','#F37321','KOSPI',12),
('034020','두산에너빌리티','두산','#005EB8','KOSPI',13),('055550','신한지주','신한','#0046FF','KOSPI',14),
('000270','기아','KIA','#05141F','KOSPI',15),('329180','HD현대중공업','HD','#0067A0','KOSPI',16),
('006400','삼성SDI','SDI','#1428A0','KOSPI',17),('068270','셀트리온','셀트','#1B8F77','KOSPI',18),
('012330','현대모비스','현대','#002C5F','KOSPI',19),('034730','SK','SK','#EA002C','KOSPI',20),
('086790','하나금융지주','하나','#009490','KOSPI',21),('035420','NAVER','N','#03C75A','KOSPI',22),
('066570','LG전자','LG','#A50034','KOSPI',23),('010120','LS ELECTRIC','LS','#003F87','KOSPI',24),
('000810','삼성화재','삼성','#1428A0','KOSPI',25),('298040','효성중공업','효성','#005BAC','KOSPI',26),
('267260','HD현대일렉트릭','HD','#0067A0','KOSPI',27),('010130','고려아연','고려','#1F5A44','KOSPI',28),
('042660','한화오션','한화','#F37321','KOSPI',29),('005490','POSCO홀딩스','POSCO','#05507D','KOSPI',30),
('132030','KODEX 골드선물(H)','KODEX','#ED1C24','ETF',31);

GRANT SELECT ON public.instrument_display_metadata TO decision_app;

CREATE TABLE public.automation_position_adoptions (
  adoption_id text PRIMARY KEY CHECK (adoption_id~'^auto_adopt_[0-9a-f]{32}$'),
  user_id text NOT NULL REFERENCES public.users(user_id) ON DELETE RESTRICT,
  account_id text NOT NULL,
  symbol text NOT NULL REFERENCES public.instrument_display_metadata(symbol),
  quantity bigint NOT NULL CHECK (quantity>0),
  entry_session date NOT NULL,
  entry_average_fill_price_krw bigint NOT NULL CHECK (entry_average_fill_price_krw>0),
  evidence_sha256 text NOT NULL CHECK (evidence_sha256~'^[0-9a-f]{64}$'),
  requested_at timestamptz NOT NULL DEFAULT statement_timestamp(),
  UNIQUE(user_id,account_id,symbol)
);

CREATE FUNCTION public.p1_adopt_historical_mock_position_v1(
  p_user_id text,p_symbol text,p_quantity bigint,p_entry_session date,
  p_entry_average_fill_price_krw bigint,p_expiry_session date,p_evidence_sha256 text
) RETURNS text
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog
AS $p1_adopt_historical_mock_position_v1$
DECLARE control_row public.automation_control%ROWTYPE;
DECLARE policy_row public.automation_policy_versions%ROWTYPE;
DECLARE position_id_value text;
DECLARE adoption_id_value text;
DECLARE synthetic_order_id text;
BEGIN
  IF session_user<>'decision_automation_runtime' OR p_user_id!~'^usr_[A-Za-z0-9_-]{8,96}$'
     OR p_symbol!~'^[0-9]{6}$' OR p_quantity<=0 OR p_entry_average_fill_price_krw<=0
     OR p_entry_session IS NULL OR p_expiry_session<=p_entry_session
     OR p_evidence_sha256!~'^[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'automation adoption input invalid' USING ERRCODE='22023';
  END IF;
  PERFORM set_config('app.automation_owner_user_id',p_user_id,true);
  PERFORM pg_advisory_xact_lock(hashtextextended('automation-control:'||p_user_id,131));
  SELECT * INTO control_row FROM public.automation_control WHERE user_id=p_user_id FOR UPDATE;
  IF NOT FOUND OR control_row.control_state<>'DISARMED' OR control_row.brokerage_mode<>'KIS_MOCK' THEN
    RAISE EXCEPTION 'automation must be disarmed for adoption' USING ERRCODE='40001';
  END IF;
  SELECT * INTO policy_row FROM public.automation_policy_versions
  WHERE policy_id=control_row.policy_id AND version=control_row.policy_version;
  IF NOT FOUND OR policy_row.max_holding_sessions IS NOT NULL THEN
    RAISE EXCEPTION 'historical adoption requires legacy policy' USING ERRCODE='40001';
  END IF;
  IF EXISTS (SELECT 1 FROM public.automation_positions p WHERE p.user_id=p_user_id AND p.account_id=control_row.account_id AND p.symbol=p_symbol AND p.status IN ('OPEN','EXIT_PENDING'))
     OR NOT EXISTS (
       SELECT 1 FROM public.portfolio_position_observations p
       WHERE p.balance_observation_id=(SELECT b.observation_id FROM public.portfolio_balance_observations b WHERE b.owner_user_id=p_user_id AND b.context_status='ACTIVE' AND b.account_scope_hash LIKE substr(control_row.account_id,6)||'%' ORDER BY b.observed_at DESC,b.received_at DESC,b.observation_id DESC LIMIT 1)
         AND p.symbol=p_symbol AND p.quantity=p_quantity
     ) THEN
    RAISE EXCEPTION 'automation adoption balance mismatch' USING ERRCODE='40001';
  END IF;
  position_id_value:='auto_pos_'||substr(encode(public.digest(convert_to(p_user_id||':'||p_symbol||':'||p_evidence_sha256,'UTF8'),'sha256'),'hex'),1,32);
  adoption_id_value:='auto_adopt_'||substr(encode(public.digest(convert_to(position_id_value||':adoption','UTF8'),'sha256'),'hex'),1,32);
  synthetic_order_id:='ord_mock_'||substr(encode(public.digest(convert_to(position_id_value||':historical-order','UTF8'),'sha256'),'hex'),1,32);
  INSERT INTO public.automation_position_adoptions(adoption_id,user_id,account_id,symbol,quantity,entry_session,entry_average_fill_price_krw,evidence_sha256)
  VALUES(adoption_id_value,p_user_id,control_row.account_id,p_symbol,p_quantity,p_entry_session,p_entry_average_fill_price_krw,p_evidence_sha256);
  INSERT INTO public.automation_positions(position_id,user_id,account_id,symbol,quantity,entry_session,expiry_session,status,bot_owned,short_allowed,created_at,entry_order_id,entry_ordered_quantity,entry_filled_quantity,entry_unfilled_quantity,entry_average_fill_price_krw,policy_id,policy_version,stop_loss_bps,take_profit_bps)
  VALUES(position_id_value,p_user_id,control_row.account_id,p_symbol,p_quantity,p_entry_session,p_expiry_session,'OPEN',true,false,statement_timestamp(),synthetic_order_id,p_quantity,p_quantity,0,p_entry_average_fill_price_krw,policy_row.policy_id,policy_row.version,policy_row.stop_loss_bps,policy_row.take_profit_bps);
  RETURN position_id_value;
END
$p1_adopt_historical_mock_position_v1$;

ALTER FUNCTION public.p1_adopt_historical_mock_position_v1(text,text,bigint,date,bigint,date,text) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.p1_adopt_historical_mock_position_v1(text,text,bigint,date,bigint,date,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.p1_adopt_historical_mock_position_v1(text,text,bigint,date,bigint,date,text) TO decision_automation_runtime;
