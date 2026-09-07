'use client';

/**
 * 최신 run 이 없을 때의 표시.
 *
 * 왜 별도 컴포넌트인가. 모델 비교·백테스트·보고서 세 화면이 같은 `useLatestRun` 을 쓰면서
 * 각자 "아직 등록된 결과가 없습니다" 한 줄만 띄웠고, 그래서 **조회 실패가 데이터 없음으로
 * 위장**됐다. 사용자는 서버가 닫힌 것을 "아직 안 만들어졌다"로 읽었다. 세 화면이 같은 규칙을
 * 쓰도록 판정을 한곳에 둔다: 확인 중 / 불러오기 실패(재시도 가능) / 진짜 없음.
 */
export function LatestRunFallback({
  pending,
  failed,
  errorMessage,
  onRetry,
  emptyText,
}: {
  pending: boolean;
  failed: boolean;
  errorMessage: string | null;
  onRetry: () => void;
  emptyText: string;
}) {
  if (pending) {
    return (
      <p className="rounded-tile border border-dashed border-rule px-4 py-6 text-[13px] leading-6 text-muted">
        불러오는 중입니다.
      </p>
    );
  }
  if (failed) {
    return (
      <div className="rounded-tile border border-block/40 px-4 py-6" role="alert">
        <p className="text-[13px] font-semibold leading-6 text-block">불러오기 실패</p>
        <p className="mt-1 text-[13px] leading-6 text-muted">
          {errorMessage ?? '최신 결과를 확인하지 못했습니다.'} 데이터가 없는 것이 아니라 조회가
          실패했습니다.
        </p>
        <button
          type="button"
          onClick={onRetry}
          className="mt-3 rounded-full border border-line px-3 py-1.5 text-[13px] text-ink"
        >
          다시 조회
        </button>
      </div>
    );
  }
  return (
    <p className="rounded-tile border border-dashed border-rule px-4 py-6 text-[13px] leading-6 text-muted">
      {emptyText}
    </p>
  );
}
