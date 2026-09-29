import type { Config } from 'tailwindcss';

const config: Config = {
  content: [
    './src/**/*.{ts,tsx}',
    '../experience-dashboard/src/shared/ui/**/*.{ts,tsx}',
    '../experience-dashboard/src/features/intro/**/*.{ts,tsx}',
  ],
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
        up: 'rgb(var(--c-up) / <alpha-value>)',
        down: 'rgb(var(--c-down) / <alpha-value>)',
      },
      fontFamily: {
        sans: ['var(--font-body)', '-apple-system', 'BlinkMacSystemFont', 'Apple SD Gothic Neo', 'Malgun Gothic', 'Noto Sans KR', 'system-ui', 'sans-serif'],
        mono: ['JetBrains Mono', 'ui-monospace', 'SFMono-Regular', 'Menlo', 'Consolas', 'monospace'],
      },
      fontSize: {
        eyebrow: ['0.6875rem', { lineHeight: '1rem', letterSpacing: '0.08em' }],
        display: ['2.25rem', { lineHeight: '2.5rem', letterSpacing: '-0.025em' }],
      },
      borderRadius: { panel: '0px', tile: '0px', control: 'var(--radius-control)', card: 'var(--radius-card)' },
      boxShadow: { card: 'none', lift: 'none', hero: 'none' },
      transitionTimingFunction: { smooth: 'cubic-bezier(0.4, 0, 0.2, 1)' },
    },
  },
  plugins: [],
};

export default config;
