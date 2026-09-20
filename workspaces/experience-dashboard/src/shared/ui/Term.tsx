'use client';

import { useEffect, useId, useRef, useState } from 'react';
import { GLOSSARY, type GlossaryKey } from '@/shared/lib/glossary';
import { useExplainMode } from '@/shared/lib/explainMode';

/**
 * 어려운 말 위에 붙는 설명 말풍선.
 *
 * 마우스만으로 열리면 키보드 사용자와 터치 사용자는 영원히 못 본다. 그래서 **버튼**으로
 * 만든다 - hover·focus·click·Esc 가 전부 동작하고 화면낭독기는 `aria-describedby` 로
 * 본문을 읽는다. `title` 속성은 쓰지 않는다(낭독기마다 다르게 읽고 터치에서는 안 뜬다).
 *
 * 설명 모드가 꺼져 있으면 **아무 표시도 하지 않는다.** 점선조차 남기지 않는다 - 끈 사람은
 * 화면이 조용하길 바라는 것이다.
 */
export function Term({ name, children }: { name: GlossaryKey; children?: React.ReactNode }) {
  const entry = GLOSSARY[name];
  const [explain] = useExplainMode();
  const [open, setOpen] = useState(false);
  const id = useId();
  const holder = useRef<HTMLSpanElement>(null);

  useEffect(() => {
    if (!open) return;
    function onKey(event: KeyboardEvent) {
      if (event.key === 'Escape') setOpen(false);
    }
    function onPointer(event: PointerEvent) {
      if (!holder.current?.contains(event.target as Node)) setOpen(false);
    }
    document.addEventListener('keydown', onKey);
    document.addEventListener('pointerdown', onPointer);
    return () => {
      document.removeEventListener('keydown', onKey);
      document.removeEventListener('pointerdown', onPointer);
    };
  }, [open]);

  const text = children ?? entry.label;
  if (!explain) return <>{text}</>;

  return (
    <span ref={holder} className="relative inline-block">
      <button
        type="button"
        aria-expanded={open}
        aria-describedby={open ? id : undefined}
        onMouseEnter={() => setOpen(true)}
        onMouseLeave={() => setOpen(false)}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        onClick={() => setOpen((value) => !value)}
        className="cursor-help border-b border-dashed border-navy/50 text-left text-inherit hover:border-navy focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-navy/40"
      >
        {text}
      </button>
      {open ? (
        <span
          id={id}
          role="tooltip"
          /*
           * 가로로는 화면을 벗어나지 않게 뷰포트 기준으로 폭을 묶고, 세로로는 위로 띄운다.
           * 말풍선이 클릭 대상을 가리면 그 아래 내용을 누를 수 없다.
           */
          className="absolute bottom-full left-0 z-30 mb-2 w-[min(20rem,calc(100vw-2rem))] rounded-card border border-line bg-panel px-4 py-3 text-left shadow-[0_8px_24px_rgba(0,0,0,0.12)]"
        >
          <span className="block text-[12px] font-semibold text-ink">{entry.label}</span>
          <span className="mt-1.5 block text-[12px] leading-5 text-muted">{entry.short}</span>
          {entry.why ? (
            <span className="mt-2 block border-t border-line pt-2 text-[12px] leading-5 text-muted">
              {entry.why}
            </span>
          ) : null}
        </span>
      ) : null}
    </span>
  );
}
