import type { Config } from 'tailwindcss';

/**
 * 디자인 토큰 (2026-09 UI 개편).
 *
 * 참조한 서비스: Apple Card(여백·큰 숫자·저채도), Revolut(카드형 블록·pill 컴포넌트),
 * 현대카드(모노톤 절제와 타이포 위계). 클론이 아니라 다음 세 가지 규칙만 가져왔다.
 *   1) 선이 아니라 여백과 그림자로 영역을 나눈다.
 *   2) 색은 상태(허용/경고/보류/차단)에만 쓰고 장식에는 쓰지 않는다.
 *   3) 화면에서 가장 큰 활자는 항상 "사용자의 돈"이다.
 */
const config: Config = {
  content: ['./src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        ink: 'rgb(var(--c-ink) / <alpha-value>)',
        muted: 'rgb(var(--c-muted) / <alpha-value>)',
        faint: 'rgb(var(--c-faint) / <alpha-value>)',
        surface: 'rgb(var(--c-surface) / <alpha-value>)',
        panel: 'rgb(var(--c-panel) / <alpha-value>)',
        subtle: 'rgb(var(--c-subtle) / <alpha-value>)',
        line: 'rgb(var(--c-line) / <alpha-value>)',
        rule: 'rgb(var(--c-rule) / <alpha-value>)',
        navy: 'rgb(var(--c-navy) / <alpha-value>)',
        brand: 'rgb(var(--c-brand) / <alpha-value>)',
        'on-brand': 'rgb(var(--c-on-brand) / <alpha-value>)',
        allow: 'rgb(var(--c-allow) / <alpha-value>)',
        warn: 'rgb(var(--c-warn) / <alpha-value>)',
        hold: 'rgb(var(--c-hold) / <alpha-value>)',
        block: 'rgb(var(--c-block) / <alpha-value>)',
        abstain: 'rgb(var(--c-abstain) / <alpha-value>)',
        /*
         * 등락색은 판정색(allow/block)과 별개다. 국내 시장 관행은 상승 적/하락 청이며
         * 미국식과 반대다. allow(초록)를 상승에 쓰면 국내 사용자에게 반대로 읽힌다.
         */
        up: 'rgb(var(--c-up) / <alpha-value>)',
        down: 'rgb(var(--c-down) / <alpha-value>)',
      },
      fontFamily: {
        sans: [
          'Pretendard Variable',
          'Pretendard',
          '-apple-system',
          'BlinkMacSystemFont',
          'Apple SD Gothic Neo',
          'Malgun Gothic',
          'Noto Sans KR',
          'system-ui',
          'sans-serif',
        ],
        mono: [
          'JetBrains Mono',
          'ui-monospace',
          'SFMono-Regular',
          'Menlo',
          'Consolas',
          'monospace',
        ],
      },
      fontSize: {
        eyebrow: ['0.6875rem', { lineHeight: '1rem', letterSpacing: '0.08em' }],
        /** 히어로 금액 전용. 화면당 1회만 쓴다. */
        display: ['2.25rem', { lineHeight: '2.5rem', letterSpacing: '-0.025em' }],
      },
      /*
       * 라운드는 실제로 누르는 대상(버튼·입력·탭 카드)에만 쓴다. 목록·표·패널은 각지다 —
       * 전부 둥글면 라운드 자체가 아무 것도 말하지 않는다. panel/tile은 하위 호환으로 0에 둔다.
       */
      borderRadius: {
        panel: '0px',
        tile: '0px',
        control: '10px',
        card: '16px',
      },
      /* 그림자를 쓰지 않는다. 위계는 괘선과 여백으로만 만든다. */
      boxShadow: {
        card: 'none',
        lift: 'none',
        hero: 'none',
      },
      transitionTimingFunction: {
        smooth: 'cubic-bezier(0.4, 0, 0.2, 1)',
      },
    },
  },
  plugins: [],
};
export default config;
