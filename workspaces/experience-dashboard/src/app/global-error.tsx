'use client';

/**
 * 루트 layout 까지 실패한 경우의 마지막 그물.
 *
 * `error.tsx` 는 route segment 안의 예외만 잡는다. `AppShell` 이나 layout 자체가 터지면
 * 그 경계는 이미 위에 있으므로 잡히지 않는다. 그때도 백지 대신 사람이 읽을 수 있는 문장이
 * 남아야 한다. 이 파일은 자체 `html`/`body` 를 렌더해야 하고, 앱 CSS 토큰을 기대할 수 없으므로
 * 색을 인라인으로 고정한다.
 */
export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <html lang="ko">
      <body style={{ margin: 0, fontFamily: 'system-ui, sans-serif', background: '#fbfbfa' }}>
        <div style={{ maxWidth: '40rem', margin: '0 auto', padding: '4rem 1.5rem' }} role="alert">
          <h1 style={{ fontSize: '20px', lineHeight: 1.4, color: '#1a1a18', margin: '0 0 12px' }}>
            앱을 시작하지 못했습니다
          </h1>
          <p style={{ fontSize: '13px', lineHeight: 1.7, color: '#6b6b66', margin: 0 }}>
            화면 골격을 그리는 단계에서 멈췄습니다. 저장된 원칙, 주문 기록, 자동운용 상태는 이
            오류로 바뀌지 않습니다. 다시 시도한 뒤에도 같으면 페이지를 새로 여세요.
          </p>
          {error.digest ? (
            <p style={{ fontSize: '12px', color: '#9a9a94', marginTop: 12 }}>
              진단 번호 {error.digest}
            </p>
          ) : null}
          <button
            type="button"
            onClick={reset}
            style={{
              marginTop: 24,
              padding: '6px 16px',
              fontSize: '13px',
              color: '#1a1a18',
              background: 'transparent',
              border: '1px solid #dcdcd6',
              borderRadius: 999,
              cursor: 'pointer',
            }}
          >
            다시 시도
          </button>
        </div>
      </body>
    </html>
  );
}
