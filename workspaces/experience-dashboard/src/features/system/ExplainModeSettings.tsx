'use client';

import { Panel } from '@/shared/ui/Panel';
import { useExplainMode } from '@/shared/lib/explainMode';
import { Term } from '@/shared/ui/Term';

/**
 * 쉬운 설명 켜고 끄기.
 *
 * 이 값은 **이 브라우저에만** 저장한다. 읽기 취향이지 계좌에 묶인 운용 설정이 아니고,
 * 서버에 두면 status 계약을 올려야 한다. 그 사실을 화면에서도 숨기지 않는다 - 다른
 * 기기에서 다시 켜야 한다는 것을 모르면 "설정이 안 저장된다"고 느낀다.
 */
export function ExplainModeSettings() {
  const [enabled, setEnabled] = useExplainMode();

  return (
    <Panel
      title="쉬운 설명"
      hint="자동운용·전략검증에 나오는 금융 용어에 밑줄을 긋고, 마우스를 올리거나 키보드로 옮기면 뜻을 말풍선으로 보여 줍니다."
      actions={
        <span className="text-eyebrow font-semibold uppercase text-faint">
          {enabled ? '켜짐' : '꺼짐'}
        </span>
      }
    >
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div className="min-w-0">
          <p className="text-[13px] leading-6 text-muted">
            예를 들어 <Term name="mdd" />, <Term name="sharpe" />, <Term name="atr" /> 같은 말에
            설명이 붙습니다. 이미 익숙하다면 꺼서 화면을 조용하게 둘 수 있습니다.
          </p>
          <p className="mt-2 text-[12px] leading-5 text-faint">
            이 설정은 지금 쓰는 브라우저에만 저장됩니다. 다른 기기에서는 다시 정해야 합니다.
          </p>
        </div>
        {/*
         * 체크박스를 쓴다. 직접 만든 스위치는 키보드·화면낭독기에서 자주 깨지고, 이 화면의
         * 다른 입력들과도 동작이 달라진다.
         */}
        <label className="flex shrink-0 cursor-pointer items-center gap-2">
          <input
            type="checkbox"
            checked={enabled}
            onChange={(event) => setEnabled(event.target.checked)}
            className="h-4 w-4 accent-[rgb(var(--c-navy))]"
          />
          <span className="text-[13px] text-ink">용어 설명 보기</span>
        </label>
      </div>
    </Panel>
  );
}
