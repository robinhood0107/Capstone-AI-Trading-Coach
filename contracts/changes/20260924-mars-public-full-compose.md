# MARS full 제품의 격리 배포 구성

`compose.public-full.yml`은 `mars-full` API·웹, PostgreSQL, Redis, actor authority,
마이그레이션, 초기 공개 RAG 적재와 RAG 런타임 seed를 독립 프로젝트·볼륨·secret root로
구성한다. Google OIDC와 Operator Vertex/Voyage 설정은 별도 full secret 파일에 두며,
사용자 KIS key·secret·계좌번호는 환경 파일에 두지 않는다. full API는 owner-bound KIS
brokerage gRPC와 전용 host KEK 디렉터리만 사용하고 실전 mode는 mock으로 고정한다.

현재 full 자동운용 실행기는 아직 단일 운영자 owner를 전제로 하므로 Compose에서 시작하지
않는다. 다중 USER claim·per-user KIS quote/order/reconciliation과 두 사용자 격리 검증 뒤
실행 flag와 공개 automation 경로를 함께 연다. 이미지 발행 준비 표식은 완료 전까지 false다.
