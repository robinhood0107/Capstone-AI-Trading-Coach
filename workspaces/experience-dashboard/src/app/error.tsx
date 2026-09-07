'use client';

import Link from 'next/link';
import { useEffect } from 'react';

/**
 * 라우트 단위 ErrorBoundary.
 *
 * 왜 필요한가. 이 앱에는 ErrorBoundary 가 없어서 렌더 중 예외 하나가 나면 화면이 백지가
 * 됐다. 서버가 예상 밖 shape 를 주면 `.map` 하나에서 터지고, 그 순간 사용자는 원칙·주문·
 * 자동운용 상태를 아무것도 볼 수 없다. 금융 화면에서 백지는 오류 문구보다 나쁘다.
 *
 * `reset()` 은 Next.js 가 주는 경계 재시도다. 데이터를 다시 불러오는 것이 아니라 이 route
 * segment 를 다시 렌더하므로, 일시적인 shape 불일치는 여기서 풀린다.
 */
export default function RouteError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    // 예외 메시지에는 서버 응답 조각이 섞일 수 있으므로 사용자 화면에 옮기지 않는다.
    // 진단에 필요한 최소값만 콘솔에 남긴다.
    console.error('route render failed', error.name, error.digest ?? '-');
  }, [error]);

  return (
    <div className="mx-auto max-w-2xl px-6 py-16" role="alert">
      <p className="text-[12px] font-medium uppercase tracking-wide text-muted">화면 오류</p>
      <h1 className="mt-2 text-[20px] font-semibold leading-7 text-ink">
        이 화면을 그리지 못했습니다
      </h1>
      <p className="mt-3 text-[13px] leading-6 text-muted">
        데이터를 받아오는 중이 아니라 화면을 그리는 단계에서 멈췄습니다. 다시 시도해도 같으면
        다른 화면으로 이동한 뒤 돌아오세요. 저장된 원칙, 주문 기록, 자동운용 상태는 이 오류로
        바뀌지 않습니다.
      </p>
      {error.digest ? (
        <p className="mt-3 font-mono text-[12px] leading-5 text-faint">진단 번호 {error.digest}</p>
      ) : null}
      <div className="mt-6 flex flex-wrap gap-2">
        <button
          type="button"
          onClick={reset}
          className="rounded-full border border-line px-4 py-1.5 text-[13px] text-ink"
        >
          다시 시도
        </button>
        <Link href="/" className="rounded-full border border-line px-4 py-1.5 text-[13px] text-muted">
          현황으로 이동
        </Link>
      </div>
    </div>
  );
}
