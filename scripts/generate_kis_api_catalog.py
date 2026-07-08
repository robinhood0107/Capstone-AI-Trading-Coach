"""KIS OpenAPI XLSX의 `API 목록` sheet에서 docs/KIS_API_카탈로그.md를 생성한다.

사용법:
    uv run --with openpyxl python scripts/generate_kis_api_catalog.py \
        <로컬 XLSX 경로> docs/KIS_API_카탈로그.md

XLSX 원본은 커밋하지 않는 로컬 자료이므로 경로를 인자로 받는다. 생성된
마크다운만 커밋하며, 카탈로그 문서는 직접 수정하지 않고 이 스크립트로
재생성한다.
"""

import argparse
import re
import sys
from collections import OrderedDict

import openpyxl

# 생성 결과가 XLSX 배포본과 1:1로 대응하는지 검증하기 위한 기준 수치.
# 수치가 달라지면 KIS가 catalog를 갱신한 것이므로 명세서 11.1의 분류
# 수치도 같은 PR에서 함께 갱신해야 한다.
SHEET_NAME = "API 목록"

# Market Calendar/Event Aggregator(API 명세서 12A) 후보 태그. 카탈로그 본문
# 수치를 바꾸지 않고 부록으로만 표기하기 위해, 배포본이 갱신돼도 순번이
# 아니라 URL로 대조한다. 선정 기준 변경은 이 상수만 고치고 재생성한다.
CALENDAR_EVENT_CANDIDATES = [
    ("/uapi/domestic-stock/v1/quotations/chk-holiday", "TradingSession(XKRX)", "1일 1회 이하 보수 호출(공식 예제 주의사항)"),
    ("/uapi/domestic-stock/v1/quotations/market-time", "TradingSession 보조(영업일)", "선물 영업일 관점 교차 검증용"),
    ("/uapi/domestic-stock/v1/quotations/news-title", "DISCLOSURE(제목)", "제목/메타만 저장, 본문 저장 금지"),
    ("/uapi/domestic-stock/v1/ksdinfo/dividend", "DIVIDEND_RECORD/DIVIDEND_PAY", "record_date/divi_pay_dt 제공"),
    ("/uapi/domestic-stock/v1/ksdinfo/paidin-capin", "RIGHTS_ISSUE", "유상증자 일정"),
    ("/uapi/domestic-stock/v1/ksdinfo/bonus-issue", "BONUS_ISSUE", "무상증자 일정"),
    ("/uapi/domestic-stock/v1/ksdinfo/merger-split", "MERGER_SPLIT", "합병/분할 일정"),
    ("/uapi/domestic-stock/v1/ksdinfo/rev-split", "SPLIT", "액면교체 일정"),
    ("/uapi/domestic-stock/v1/ksdinfo/cap-dcrs", "CAPITAL_REDUCTION", "자본감소 일정"),
    ("/uapi/domestic-stock/v1/ksdinfo/list-info", "IPO_LISTING", "상장정보 일정"),
    ("/uapi/domestic-stock/v1/ksdinfo/pub-offer", "IPO_SUBSCRIPTION", "공모주 청약 일정"),
    ("/uapi/domestic-stock/v1/ksdinfo/sharehld-meet", "SHAREHOLDER_MEETING", "주주총회 일정"),
    ("/uapi/domestic-stock/v1/ksdinfo/purreq", "MERGER_SPLIT 보조", "주식매수청구 일정"),
    ("/uapi/domestic-stock/v1/ksdinfo/forfeit", "RIGHTS_ISSUE 보조", "실권주 일정"),
    ("/uapi/domestic-stock/v1/ksdinfo/mand-deposit", "참고", "의무예치 일정"),
    ("/uapi/domestic-stock/v1/quotations/estimate-perform", "EARNINGS_EXPECTED 보조", "추정실적 — 확정 아님, TENTATIVE 유지"),
    ("/uapi/overseas-stock/v1/quotations/countries-holiday", "해외 결제일/휴장 참고", "TradingSession 보조 교차 검증"),
    ("/uapi/overseas-price/v1/quotations/news-title", "해외 DISCLOSURE(제목)", "제목/메타만 저장"),
]


def cell(v):
    # 표 구분자와 충돌하지 않도록 셀 안의 pipe를 이스케이프한다.
    return str(v).strip().replace("|", "\\|") if v is not None else ""


def mock_label(real_tr, mock_tr, mock_domain):
    # "모의 지원"을 단일 값으로 단정하지 않기 위해 Domain과 TR_ID를
    # 분리해 판정한다(최종_프로젝트_명세서 11.1 기준).
    if mock_domain.startswith(("http", "ws")):
        if not mock_tr:
            return "지원(Domain 전환)"
        if mock_tr == real_tr:
            return "지원(동일 TR)"
        return "지원(모의 TR 분리)"
    return "미지원"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("xlsx", help="KIS OpenAPI 전체문서 XLSX 경로(로컬 전용, 커밋 금지)")
    parser.add_argument("output", help="생성할 마크다운 경로")
    args = parser.parse_args()

    wb = openpyxl.load_workbook(args.xlsx, read_only=True, data_only=True)
    ws = wb[SHEET_NAME]
    rows = [r for r in ws.iter_rows(values_only=True)][1:]
    rows = [r for r in rows if r[0] is not None]

    # 파일명에 포함된 배포 기준일(YYYYMMDD)을 그대로 문서에 남겨
    # 어느 배포본에서 생성했는지 추적 가능하게 한다.
    m = re.search(r"(\d{8})", args.xlsx)
    base_date = m.group(1) if m else "unknown"

    total = len(rows)
    rest = sum(1 for r in rows if cell(r[1]).upper() == "REST")
    wsock = total - rest
    labeled = [
        (r, mock_label(cell(r[5]), cell(r[6]), cell(r[10])))
        for r in rows
    ]
    mock_ok = sum(1 for _, lab in labeled if lab != "미지원")
    mock_tr = sum(1 for _, lab in labeled if lab in ("지원(동일 TR)", "지원(모의 TR 분리)"))

    groups = OrderedDict()
    for r, lab in labeled:
        groups.setdefault(cell(r[2]) or "(분류 없음)", []).append((r, lab))

    out = []
    out.append("# KIS OpenAPI 전체 카탈로그 (자동 생성)")
    out.append("")
    out.append(f"기준 자료: `한국투자증권_오픈API_전체문서_{base_date}` XLSX `API 목록` sheet (KIS 공식 배포, 모의 지원 경계의 단일 진실 소스)")
    out.append("")
    out.append("이 문서는 `scripts/generate_kis_api_catalog.py`로 생성한다. 직접 수정하지 않고, XLSX 배포본이 갱신되면 재생성해서 커밋한다.")
    out.append("")
    out.append("| 요약 | 값 |")
    out.append("|---|---|")
    out.append(f"| 전체 API | {total} (REST {rest}, WebSocket {wsock}) |")
    out.append(f"| 모의투자 Domain 지원 | {mock_ok} |")
    out.append(f"| 명시적 모의 TR_ID 보유 | {mock_tr} |")
    out.append(f"| 모의투자 미지원 | {total - mock_ok} |")
    out.append("")
    out.append("모의 지원 판정: `모의 Domain`에 모의투자용 URL이 있으면 지원으로 보고, `모의 TR_ID`가 실전과 같은지/분리인지/없는지(OAuth 계열)를 구분해 표기한다.")
    out.append("")

    for name, items in groups.items():
        out.append(f"## {name} ({len(items)}개)")
        out.append("")
        out.append("| 순번 | API 명 | API ID | Method | URL | 실전 TR_ID | 모의 TR_ID | 모의 지원 |")
        out.append("|---|---|---|---|---|---|---|---|")
        for r, lab in items:
            comm = cell(r[1]).upper()
            method = cell(r[7]) if comm == "REST" else "WS"
            out.append(
                f"| {cell(r[0])} | {cell(r[3])} | {cell(r[4])} | {method} "
                f"| `{cell(r[8])}` | {cell(r[5]) or '-'} | {cell(r[6]) or '-'} | {lab} |"
            )
        out.append("")

    # 부록은 본문 수치를 건드리지 않는 태그 전용 절이다. 배포본 갱신으로
    # 순번이 바뀌어도 URL 대조로 따라가고, 사라진 API는 재확인 대상으로
    # 표기해 조용히 누락되지 않게 한다.
    out.append("## 부록 A. Market Calendar/Event Aggregator 후보 태그 (스크립트 관리)")
    out.append("")
    out.append("> 변경 반영(2026-07-08): Market Calendar/Event 후보 태그 부록을 추가함(본문 카탈로그 수치는 변경 없음).")
    out.append("")
    out.append(
        "API 명세서 12A(계획)의 수집 후보를 URL 기준으로 태그한다. 본문 수치/분류에는 영향이 없으며, "
        "선정 기준은 `scripts/generate_kis_api_catalog.py`의 `CALENDAR_EVENT_CANDIDATES` 상수로만 관리한다. "
        "아래 항목은 전부 모의투자 미지원이므로 최종_프로젝트_명세서 12.5의 live read-only 경계에서만 호출할 수 있다."
    )
    out.append("")
    out.append("| 순번 | API 명 | URL | 이벤트 매핑 | 비고 |")
    out.append("|---|---|---|---|---|")
    by_url = {cell(r[8]): r for r, _ in labeled}
    for url, tag, note in CALENDAR_EVENT_CANDIDATES:
        r = by_url.get(url)
        if r is not None:
            out.append(f"| {cell(r[0])} | {cell(r[3])} | `{url}` | {tag} | {note} |")
        else:
            out.append(f"| - | (배포본 미수록 — 재확인 필요) | `{url}` | {tag} | {note} |")
    out.append("")

    with open(args.output, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(out))
    print(f"generated: {args.output} ({total} APIs, {len(groups)} groups)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
