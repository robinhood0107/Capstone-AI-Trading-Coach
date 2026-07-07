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

    with open(args.output, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(out))
    print(f"generated: {args.output} ({total} APIs, {len(groups)} groups)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
