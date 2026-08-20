-- LightGBM을 연구 전용으로 고정하고 production stage/activation/publication capability를 회수한다.
-- V73의 immutable audit tables와 functions는 재현 증거를 위해 삭제하지 않는다.

REVOKE ALL PRIVILEGES ON FUNCTION
  public.stage_signal_model_release(text,text,text,text,text,text,text,text,text,text,text),
  public.stage_signal_batch(text,text,text,text,text,text,date,timestamptz,text)
FROM decision_signal_writer;

REVOKE ALL PRIVILEGES ON FUNCTION
  public.publish_active_signal_batch(text,text,text),
  public.suspend_signal_model_for_drift(text,text)
FROM decision_signal_scheduler;

REVOKE ALL PRIVILEGES ON FUNCTION
  public.activate_signal_model_and_batch(text,text,text,text,text,text,text),
  public.suspend_signal_model_for_drift(text,text)
FROM decision_signal_admin;
