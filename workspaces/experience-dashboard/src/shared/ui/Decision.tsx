import type { DecisionAction } from '@/shared/api/wire';

const TONE: Record<DecisionAction, { label: string; bg: string; text: string; border: string; gloss: string }> = {
  ALLOW: {
    label: '허용',
    bg: 'bg-allow',
    text: 'text-allow',
    border: 'border-allow',
    gloss: '원칙과 안전장치를 모두 통과했습니다.',
  },
  WARN: {
    label: '경고',
    bg: 'bg-warn',
    text: 'text-warn',
    border: 'border-warn',
    gloss: '진행할 수 있지만 확인이 필요한 사유가 있습니다.',
  },
  HOLD: {
    label: '보류',
    bg: 'bg-hold',
    text: 'text-hold',
    border: 'border-hold',
    gloss: '필수 근거가 없어 판단을 미뤘습니다. 오류가 아니라 정상 판정입니다.',
  },
  BLOCK: {
    label: '차단',
    bg: 'bg-block',
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

export function DecisionBadge({ status, size = 'md' }: { status: DecisionAction; size?: 'sm' | 'md' }) {
  const tone = TONE[status];
  const dims = size === 'sm' ? 'px-2 py-0.5 text-[11px]' : 'px-3 py-1 text-[13px]';
  return (
    <span
      className={`inline-flex items-center gap-2 rounded-[1px] ${tone.bg} ${dims} font-semibold text-white`}
    >
      <span className="font-mono tracking-[0.08em]">{status}</span>
      <span className="font-sans font-medium opacity-90">{tone.label}</span>
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
      <div className="flex items-stretch gap-px border border-line bg-line">
        {PRECEDENCE.map((stop, index) => {
          const active = index === activeIndex;
          const tone = TONE[stop];
          return (
            <div
              key={stop}
              aria-current={active ? 'step' : undefined}
              className={`flex-1 px-3 py-3 ${active ? `${tone.bg} text-white` : 'bg-panel text-faint'}`}
            >
              <p className="font-mono text-eyebrow tracking-[0.14em]">{stop}</p>
              <p className={`mt-1 text-[13px] font-medium ${active ? 'text-white' : 'text-faint'}`}>
                {tone.label}
              </p>
            </div>
          );
        })}
      </div>
      <p className="mt-2 font-mono text-eyebrow uppercase text-faint">
        우선순위 낮음 → 높음 · 상위 등급이 하나라도 성립하면 그 판정이 최종입니다
      </p>
    </div>
  );
}

/** ABSTAIN은 판정이 아니다. 모델이 근거를 내지 못한 상태이므로 색을 주지 않는다. */
export function AbstainChip({ reason }: { reason: string }) {
  return (
    <span
      className="hatch inline-flex items-center gap-2 rounded-[1px] border border-line px-2 py-0.5 font-mono text-[11px] uppercase tracking-[0.08em] text-faint"
      title={reason}
    >
      ABSTAIN
    </span>
  );
}
