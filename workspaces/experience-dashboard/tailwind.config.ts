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
        ink: '#0C0F14',
        muted: '#59616D',
        faint: '#8B939F',
        surface: '#F4F5F7',
        panel: '#FFFFFF',
        /** 카드 안에서 한 단계 더 들어간 영역(타일, 표 헤더)의 배경. */
        subtle: '#F7F8FA',
        line: '#E9EBEF',
        rule: '#DFE3E9',
        navy: '#14213D',
        allow: '#067A46',
        warn: '#B25E09',
        hold: '#4A5462',
        block: '#B3261E',
        abstain: '#98A0AB',
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
      borderRadius: {
        panel: '20px',
        tile: '14px',
      },
      boxShadow: {
        card: '0 1px 2px 0 rgb(12 15 20 / 0.04), 0 12px 32px -18px rgb(12 15 20 / 0.18)',
        lift: '0 2px 4px 0 rgb(12 15 20 / 0.05), 0 20px 44px -22px rgb(12 15 20 / 0.26)',
        hero: '0 1px 2px 0 rgb(12 15 20 / 0.06), 0 28px 60px -30px rgb(20 33 61 / 0.45)',
      },
      transitionTimingFunction: {
        smooth: 'cubic-bezier(0.4, 0, 0.2, 1)',
      },
    },
  },
  plugins: [],
};
export default config;
