# 2026-09-20 후속 TODO 를 private-reference 로 옮긴다

## 범위

`docs/자동운용_후속_TODO.md` 를 저장소에서 제거하고, 그 내용을
`private-reference/todo/TODO-P2.md` §1~3 으로 옮긴다. 저장소에 흩어져 있던 TODO 를
한 곳으로 모으는 정리의 일부다.

변경 대상은 넷이다.

| 파일 | 변경 |
|---|---|
| `docs/자동운용_후속_TODO.md` | 삭제 |
| `docs/README.md` | 문서 목록에서 해당 줄 제거 |
| `docs/최종_프로젝트_명세서.md` | 링크를 평서문으로. "아직 구현하지 않은 후속 과제다" |
| `contracts/verify_pre_s5_doc_truth_freeze.py` | `P1_CURRENT_MUTABLE_DOCUMENTS` 에 추가 (삭제를 허용하는 유일한 문) |

## 선행 기록의 개정

`contracts/changes/20260907-owner-stop-ridge-and-runtime-data.md:24` 가 이 문서를
링크로 걸고 있다. `contracts/changes/**` 는 신규 추가만 허용되고 기존 파일 수정이
금지되므로 그 줄은 그대로 둔다. **이 기록이 그 링크를 대체한다.**

해당 문장이 가리키던 세 항목 — 실시간 시세·계좌·UI, LSTM 다기간 예측, 모델 성능 비교
— 는 `private-reference/todo/TODO-P2.md` §1·§2·§3 에 그대로 있다. 내용은 줄지
않았고 위치만 바뀌었다.

20260907 기록의 실질 조항은 유지된다. 고정 50:50 은 임시 운용 정책이고 비교 검증
완료를 뜻하지 않으며, 실제 외부 호출·장중 체결 성공을 코드/fixture 검증만으로
주장하지 않는다.

## 권한과 비권한

권한: 위 네 파일의 변경. `private-reference/todo/` 신설.

비권한: `contracts/changes/**` 기존 파일 수정, 이력 재작성, 공개 문서의 다른 삭제,
`REQUIRED_PUBLIC_MARKERS`·`SOLO_OWNERSHIP_MARKERS` 변경.

## 외부 실행 상한

없다. 문서 이동과 계약 목록 갱신뿐이며 provider·계좌·주문 호출은 0이다.
