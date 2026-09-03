import type { DecisionAction } from '@/shared/api/wire';

const TONE: Record<
  DecisionAction,
  { label: string; bg: string; tint: string; text: string; border: string; gloss: string }
> = {
  ALLOW: {
    label: '허용',
    bg: 'bg-allow',
    tint: 'bg-allow/10',
    text: 'text-allow',
    border: 'border-allow',
    gloss: '원칙과 안전장치를 모두 통과했습니다.',
  },
  WARN: {
    label: '경고',
    bg: 'bg-warn',
    tint: 'bg-warn/10',
    text: 'text-warn',
    border: 'border-warn',
    gloss: '진행할 수 있지만 확인이 필요한 사유가 있습니다.',
  },
  HOLD: {
    label: '보류',
    bg: 'bg-hold',
    tint: 'bg-hold/10',
    text: 'text-hold',
    border: 'border-hold',
    gloss: '필수 근거가 없어 판단을 미뤘습니다. 오류가 아니라 정상 판정입니다.',
  },
  BLOCK: {
    label: '차단',
    bg: 'bg-block',
    tint: 'bg-block/10',
    text: 'text-block',
    border: 'border-block',
    gloss: '차단 등급 원칙 위반이 확인됐습니다.',
  },
};

/** 우선순위: BLOCK > HOLD > WARN > ALLOW (최종 명세서 8.4.1) */
const PRECEDENCE: DecisionAction[] = ['ALLOW', 'WARN', 'HOLD', 'BLOCK'];

export function decisionGloss(status: DecisionAction): string {
  return TONE[status].gloss;
}

/**
 * 배지는 채도 높은 면 대신 옅은 톤 + 점으로 바꿨다.
 * 한 화면에 배지가 10개 넘게 깔리는 표에서 원색 블록은 판정 자체보다 시끄러워진다.
 * 영문 토큰(ALLOW…)은 계약값이므로 지우지 않고 뒤에 작게 유지한다.
 */
export function DecisionBadge({ status, size = 'md' }: { status: DecisionAction; size?: 'sm' | 'md' }) {
  const tone = TONE[status];
  const dims = size === 'sm' ? 'px-2.5 py-0.5 text-[12px]' : 'px-3 py-1 text-[13px]';
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full ${tone.tint} ${dims} font-semibold ${tone.text}`}
    >
      <span aria-hidden className={`h-1.5 w-1.5 rounded-full ${tone.bg}`} />
      {tone.label}
      <span className="font-mono text-[10px] font-medium tracking-[0.06em] opacity-60">{status}</span>
    </span>
  );
}

/**
 * 판정 레일 — 이 대시보드의 signature.
 * 4개 정지점을 우선순위 순서로 깔고 현재 판정만 점등한다.
 * 사용자가 "왜 BLOCK이 WARN을 이기는가"를 화면 구조에서 바로 읽게 하는 것이 목적이다.
 */
export function DecisionRail({ status }: { status: DecisionAction }) {
  const activeIndex = PRECEDENCE.indexOf(status);
  return (
    <div>
      <div className="flex items-stretch gap-px overflow-hidden rounded-tile bg-line">
        {PRECEDENCE.map((stop, index) => {
          const active = index === activeIndex;
          const tone = TONE[stop];
          return (
            <div
              key={stop}
              aria-current={active ? 'step' : undefined}
              className={`flex-1 px-3 py-3 ${active ? `${tone.bg} text-white` : 'bg-subtle text-faint'}`}
            >
              <p className="font-mono text-eyebrow tracking-[0.12em]">{stop}</p>
              <p className={`mt-1 text-[13px] font-medium ${active ? 'text-white' : 'text-faint'}`}>
                {tone.label}
              </p>
            </div>
          );
        })}
      </div>
      <p className="mt-2 text-[11px] leading-5 text-faint">
        우선순위 낮음 → 높음 · 상위 등급이 하나라도 성립하면 그 판정이 최종입니다
      </p>
    </div>
  );
}

/** ABSTAIN은 판정이 아니다. 모델이 근거를 내지 못한 상태이므로 색을 주지 않는다. */
export function AbstainChip({ reason }: { reason: string }) {
  return (
    <span
      className="hatch inline-flex items-center gap-2 rounded-full border border-line px-2.5 py-0.5 font-mono text-[11px] uppercase tracking-[0.06em] text-faint"
      title={reason}
    >
      ABSTAIN
    </span>
  );
}
