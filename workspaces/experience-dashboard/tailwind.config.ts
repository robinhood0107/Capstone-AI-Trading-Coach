import type { Config } from 'tailwindcss';

const config: Config = {
  content: ['./src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        ink: '#14181D',
        muted: '#5C6672',
        faint: '#8A9099',
        surface: '#EEF0EC',
        panel: '#FFFFFF',
        line: '#D5D9D1',
        rule: '#BFC6BC',
        navy: '#1D3557',
        allow: '#16653F',
        warn: '#9A6510',
        hold: '#43505F',
        block: '#96262B',
        abstain: '#8A9099',
      },
      fontFamily: {
        sans: [
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
        eyebrow: ['0.6875rem', { lineHeight: '1rem', letterSpacing: '0.12em' }],
      },
      borderRadius: { panel: '2px' },
    },
  },
  plugins: [],
};
export default config;
