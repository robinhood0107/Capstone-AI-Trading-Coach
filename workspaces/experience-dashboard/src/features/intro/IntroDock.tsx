'use client';

import { useEffect, useRef, useState } from 'react';
import { cx } from './acts/cx';
import type { IntroSet } from './introSet';

/**
 * 우하단 도크 — 테마 세트 전환기와 이동 버튼.
 *
 * 본문 한가운데를 떠다니면 읽는 데 방해가 되므로 화면 구석에 작게 앉힌다.
 */
export function IntroDock({
  introSet,
  onChangeSet,
  atEnd,
  onJump,
  endLabel,
}: {
  introSet: IntroSet;
  onChangeSet: (next: IntroSet) => void;
  atEnd: boolean;
  onJump: () => void;
  endLabel: string;
}) {
  const [open, setOpen] = useState(false);
  const themerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onClick = (event: MouseEvent) => {
      if (!themerRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false);
    };
    document.addEventListener('click', onClick);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('click', onClick);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  return (
    <div className={cx('dock')}>
      <div className={cx('themer')} id="themer" data-open={String(open)} ref={themerRef}>
        <button
          className={cx('themer__btn')}
          type="button"
          aria-expanded={open}
          aria-controls="introThemerPanel"
          onClick={() => setOpen((prev) => !prev)}
        >
          <span className={cx('themer__sw')} aria-hidden="true" />
          <span>테마</span>
          <span className={cx('themer__car')} aria-hidden="true">
            ▾
          </span>
        </button>
        <div className={cx('themer__wrap')}>
          <div className={cx('themer__in')}>
            <div
              className={cx('themer__pad')}
              id="introThemerPanel"
              role="radiogroup"
              aria-label="테마 선택"
            >
              <SetOption
                value="light"
                name="밝게"
                sub="Tally → NAJM"
                active={introSet === 'light'}
                onChoose={(next) => {
                  onChangeSet(next);
                  setOpen(false);
                }}
              />
              <SetOption
                value="dark"
                name="어둡게"
                sub="Wayfare → Press"
                active={introSet === 'dark'}
                onChoose={(next) => {
                  onChangeSet(next);
                  setOpen(false);
                }}
              />
            </div>
          </div>
        </div>
      </div>
      <button
        className={cx('pillbtn')}
        type="button"
        aria-label={atEnd ? '소개 처음으로 이동' : `${endLabel}(으)로 이동`}
        onClick={onJump}
      >
        {atEnd ? '소개 처음으로 ↑' : `${endLabel} ↓`}
      </button>
    </div>
  );
}

function SetOption({
  value,
  name,
  sub,
  active,
  onChoose,
}: {
  value: IntroSet;
  name: string;
  sub: string;
  active: boolean;
  onChoose: (value: IntroSet) => void;
}) {
  return (
    <button
      className={cx('opt', value === 'light' ? 'opt--l' : 'opt--d')}
      type="button"
      role="radio"
      aria-checked={active}
      onClick={() => onChoose(value)}
    >
      <span className={cx('opt__d')} aria-hidden="true" />
      <span>
        <span className={cx('opt__n')}>{name}</span>
        <span className={cx('opt__s')}>{sub}</span>
      </span>
      <span className={cx('opt__c')} aria-hidden="true">
        ●
      </span>
    </button>
  );
}
