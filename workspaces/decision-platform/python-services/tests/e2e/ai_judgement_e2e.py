"""AI 판단이 자동매매에 실제로 닿는지 실행 중인 스택에서 확인한다.

이 파일은 테스트 전용이다. production 이미지나 business logic에서 import 하지 않으며 `test_`
접두사가 아니라 pytest 수집 대상도 아니다.

왜 이것이 따로 필요한가. 엔진 단위 테스트는 판단이 순위와 수량을 바꾼다는 것을 보이지만,
그 상태와 그 기록이 **배포된 DB에 실제로 존재하는지**는 말하지 못한다. 상태기 화이트리스트가
DB 전이 함수와 한 글자라도 어긋나면 tick이 CAS 충돌로 죽는데, 그 어긋남은 코드를 읽어서는
보이지 않고 돌려 봐야 보인다.

무엇을 확인하나.
  1. `AI_JUDGING`이 두 상태 CHECK 제약과 DB 전이 함수에 모두 들어 있다. 셋 중 하나만 빠져도
     그 자리에서 자동매매가 죽는다.
  2. 엔진의 전이 표와 DB 전이 함수가 정확히 같은 집합이다.
  3. 판단 기록 테이블이 소유자 세션에 열려 있고 키 봉투 같은 것은 없다.
  4. 판단 기록의 쓰기 경로는 definer 함수뿐이다 - 자동운용 role에 표 권한이 없다.
  5. 수량은 줄기만 한다. 그 불변식이 코드가 아니라 DB 제약으로도 서 있다.
  6. Strong LLM 설정 표면이 열려 있고, 키는 마지막 네 글자만 나온다.

무엇을 확인하지 않나. 실제 provider를 불러 판단을 받아 오지는 않는다. 그것은 외부 호출이고
비용이며, 판단이 순위와 수량을 어떻게 바꾸는지는 `tests/p1_owner/test_automation_ai_judgement.py`
20개가 결정론적으로 이미 덮는다. 여기서 보는 것은 그 경로가 배포에 실재하는지다.

실행:
  P1_AI_JUDGEMENT_E2E=1 python -m tests.e2e.ai_judgement_e2e \\
    --out artifacts/decision-platform/e2e/ai-judgement.json
"""

from __future__ import annotations

import argparse
import re
import sys
import uuid
from pathlib import Path
from typing import Final

from .harness import (
    Api,
    HarnessError,
    Recorder,
    psql,
    require_opt_in,
    write_report,
)

_OPT_IN: Final = "P1_AI_JUDGEMENT_E2E"
_MIGRATIONS: Final = Path(__file__).resolve().parents[3] / (
    "spring-api/src/main/resources/db/migration"
)
_RUNTIME_ROLE: Final = "decision_automation_runtime"


def check_state_is_installed(recorder: Recorder) -> None:
    """AI_JUDGING이 두 CHECK 제약과 전이 함수에 모두 있어야 한다."""

    constraints = psql(
        "select count(*) from pg_constraint"
        " where conname in ('automation_runs_state_check',"
        " 'automation_runtime_checkpoint_state_check')"
        " and pg_get_constraintdef(oid) like '%AI_JUDGING%';"
    ).strip()
    transitions = psql(
        "select count(*) from pg_proc"
        " where proname = 'p1_automation_transition_valid_v2'"
        " and prosrc like '%AI_JUDGING%';"
    ).strip()
    recorder.add(
        "AI_JUDGING 상태가 배포에 실재한다",
        "PASS" if constraints == "2" and transitions == "1" else "FAIL",
        f"state CHECK={constraints}/2 전이 함수={transitions}/1"
        " (하나라도 빠지면 후보 선정 앞에서 자동매매가 죽는다)",
    )


def check_engine_and_database_agree(recorder: Recorder) -> None:
    """엔진 표와 DB 전이 함수가 같은 집합이어야 한다."""

    from app.p1_owner.automation import _LEGAL_TRANSITIONS

    # 버전 순으로 본다. 파일 이름을 사전순으로 정렬하면 V93이 V106보다 뒤에 와서 옛 정의를
    # 최신으로 읽고, 그러면 어긋남이 있어도 초록불이 뜬다.
    source = ""
    for _, path in sorted(
        (int(re.match(r"V(\d+)__", item.name).group(1)), item)  # type: ignore[union-attr]
        for item in _MIGRATIONS.glob("V*__*.sql")
    ):
        body = path.read_text(encoding="utf-8")
        if "CREATE OR REPLACE FUNCTION public.p1_automation_transition_valid_v2" in body:
            source = body
    if not source:
        raise HarnessError("전이 함수를 정의한 마이그레이션이 없다")
    start = source.index("$p1_automation_transition_valid_v2$")
    end = source.index("$p1_automation_transition_valid_v2$;", start)
    database = frozenset(re.findall(r"\('([A-Z_]+)','([A-Z_]+)'\)", source[start:end]))
    only_engine = sorted(_LEGAL_TRANSITIONS - database)
    only_database = sorted(database - _LEGAL_TRANSITIONS)
    recorder.add(
        "엔진 전이 표와 DB 전이 함수가 같다",
        "PASS" if not only_engine and not only_database else "FAIL",
        f"엔진만={only_engine} DB만={only_database}"
        " (좁으면 tick이 CAS 충돌로 죽고 넓으면 잘못된 전이가 durable하게 남는다)",
    )


def check_judgement_record_is_owner_readable(recorder: Recorder) -> None:
    """기록은 소유자 화면이 읽을 수 있어야 하고, 그 안에 키 봉투는 없어야 한다."""

    columns = psql(
        "select string_agg(column_name, ',' order by column_name)"
        " from information_schema.columns"
        " where table_name = 'automation_ai_judgements';"
    ).strip()
    present = set(columns.split(",")) if columns else set()
    required = {
        "baseline_symbol",
        "selected_symbol",
        "participation",
        "confidence_bps",
        "quantity_before",
        "quantity_after",
        "vetoed_symbol_count",
    }
    # 확신도는 정수 basis point로만 산다. 부동소수로 저장하면 같은 판단이 저장 왕복에서 달라져
    # 그 수량이 왜 나왔는지 재현할 수 없다.
    confidence_type = psql(
        "select data_type from information_schema.columns"
        " where table_name = 'automation_ai_judgements' and column_name = 'confidence_bps';"
    ).strip()
    recorder.add(
        "판단 기록이 무엇을 남기는가",
        "PASS" if required <= present and confidence_type == "integer" else "FAIL",
        f"빠진 열={sorted(required - present)} confidence 타입={confidence_type or '<없음>'}"
        " (규칙만으로 고른 1등과 실제 선택, 축소 전후가 함께 있어야 AI가 무엇을 바꿨는지 말할 수 있다)",
    )


def check_write_path_is_definer_only(recorder: Recorder) -> None:
    """자동운용 role은 표 권한 없이 definer 함수로만 쓴다."""

    table_grants = psql(
        "select coalesce(string_agg(privilege_type, ','), '')"
        " from information_schema.table_privileges"
        f" where grantee = '{_RUNTIME_ROLE}' and table_name = 'automation_ai_judgements';"
    ).strip()
    routines = psql(
        "select count(*) from information_schema.routine_privileges"
        f" where grantee = '{_RUNTIME_ROLE}' and privilege_type = 'EXECUTE'"
        " and routine_name in ('p1_record_automation_ai_judgement_v1',"
        " 'p1_read_automation_ai_judgement_v1');"
    ).strip()
    recorder.add(
        "판단 기록 쓰기는 definer 함수뿐이다",
        "PASS" if table_grants == "" and routines == "2" else "FAIL",
        f"표 권한=[{table_grants}] 함수 EXECUTE={routines}/2 (표 권한이 하나라도 생기면 회귀다)",
    )


def check_size_only_shrinks(recorder: Recorder) -> None:
    """AI는 수량을 늘리지 못한다. 그 불변식이 DB 제약으로도 서 있어야 한다."""

    shrink = psql(
        "select count(*) from pg_constraint"
        " where conname = 'automation_ai_judgements_size_only_shrinks_check';"
    ).strip()
    recorder.add(
        "수량은 줄기만 한다",
        "PASS" if shrink == "1" else "FAIL",
        f"제약={shrink}/1 (코드가 실수해도 늘어난 수량은 저장되지 않는다)",
    )


def check_settings_surface(recorder: Recorder, api: Api) -> None:
    """설정 표면이 열려 있고 키는 마지막 네 글자만 나와야 한다."""

    status, body = api.request("GET", "/api/v2/rag/corpus-status")
    fields = {key for key in body if key.startswith("strongLlm")}
    # 키처럼 생긴 리터럴은 비밀 검사기를 늘 부른다. 서버 형식만 만족하는 값을 여기서 만든다.
    probe_key = f"probe-{uuid.uuid4().hex}"
    written, _ = api.request(
        "PUT",
        "/api/v2/strong-llm/settings",
        {
            "provider": "openai",
            "fallbackProvider": None,
            "modelId": None,
            "fallbackModelId": None,
            "baseUrl": None,
            "fallbackBaseUrl": None,
            "answerLanguage": "ko",
            "dailyGenerateCallCap": 30,
            "apiKey": probe_key,
        },
        headers={"X-Request-Id": f"req_{uuid.uuid4().hex}"},
    )
    _, after = api.request("GET", "/api/v2/rag/corpus-status")
    last4 = after.get("strongLlmKeyLast4")
    # 저장된 암호문 어디에도 평문이 없어야 한다. 마지막 네 글자만 밖으로 나온다.
    plaintext = psql(
        f"select coalesce(sum(position('{probe_key}' in encode(key_ciphertext,'escape'))), 0)"
        " from strong_llm_owner_credentials;"
    ).strip()
    api.request(
        "PUT",
        "/api/v2/strong-llm/settings",
        {
            "provider": "vertex",
            "fallbackProvider": None,
            "modelId": None,
            "fallbackModelId": None,
            "baseUrl": None,
            "fallbackBaseUrl": None,
            "answerLanguage": "ko",
            "dailyGenerateCallCap": 50,
            "apiKey": "",
        },
        headers={"X-Request-Id": f"req_{uuid.uuid4().hex}"},
    )
    recorder.add(
        "설정은 저장되고 키는 마지막 네 글자만 나온다",
        "PASS"
        if status == 200
        and written == 200
        and last4 == probe_key[-4:]
        and plaintext == "0"
        and len(fields) == 10
        else "FAIL",
        f"status={status} 저장={written} 노출={last4} 평문발견={plaintext} 설정필드={len(fields)}/10"
        " (저장 응답은 HTTP 200 empty body이고 읽기에는 마지막 네 글자만 실린다)",
    )


def main(argv: list[str]) -> int:
    require_opt_in(_OPT_IN)
    parser = argparse.ArgumentParser(description="AI 판단 경로가 배포에 실재하는지 확인한다")
    parser.add_argument("--out", default="")
    args = parser.parse_args(argv[1:])

    recorder = Recorder()
    try:
        check_state_is_installed(recorder)
        check_engine_and_database_agree(recorder)
        check_judgement_record_is_owner_readable(recorder)
        check_write_path_is_definer_only(recorder)
        check_size_only_shrinks(recorder)
        owner = Api()
        owner.login("demo-user")
        check_settings_surface(recorder, owner)
    except HarnessError as error:
        recorder.add("확인 중단", "FAIL", str(error))
    except Exception as error:  # noqa: BLE001 - 어떤 실패든 판정으로 남긴다
        recorder.add("확인 중단", "FAIL", f"{type(error).__name__}: {error}")

    report = write_report(
        contract_id="p1-ai-judgement-e2e.v1",
        marker="P1_AI_JUDGEMENT_E2E",
        recorder=recorder,
        out=args.out,
    )
    return 1 if report["verdict"] == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
