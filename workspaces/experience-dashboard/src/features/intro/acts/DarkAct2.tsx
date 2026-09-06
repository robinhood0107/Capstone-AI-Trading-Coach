'use client';

import { useId, useState } from 'react';
import { cx } from './cx';
import { ScrollCue } from './ScrollCue';
import {
  PRESETS,
  PRESET_LABELS,
  fillPercent,
  judge,
  type Dials,
  type Preset,
  type PresetKey,
} from './playground';

/**
 * 어둡게 2막 — Press. 원칙 다이얼을 직접 움직이는 유일한 막.
 *
 * 데모에서는 DOM id 를 잡아 textContent 를 갈아 끼웠다. 여기서는 상태 하나(`preset` + `dials`)
 * 에서 판정까지 전부 파생시킨다 — 계산은 `playground.ts` 가 맡고 이 파일은 그리기만 한다.
 */
export function DarkAct2() {
  const [presetKey, setPresetKey] = useState<PresetKey>('bal');
  const [dials, setDials] = useState<Dials>(() => {
    const { loss, gold, pos } = PRESETS.bal;
    return { loss, gold, pos };
  });
  // 다이얼을 직접 움직여도 손절·보유기간처럼 고정된 값은 고른 프리셋을 따른다.
  const fixed: Preset = PRESETS[presetKey];
  const { rows, blocked, line } = judge(dials);

  function applyPreset(key: PresetKey) {
    const preset = PRESETS[key];
    setPresetKey(key);
    setDials({ loss: preset.loss, gold: preset.gold, pos: preset.pos });
  }

  return (
    <section className={cx('act', 'act--2')} id="dark2">
      <nav className={cx('prail')} aria-hidden="true">
        <span>MARS · 원칙 검증</span>
        <i />
        <span>KIS 계좌 자동매매</span>
      </nav>

      <div className={cx('p2', 'wrap')}>
        <span className={cx('eyebrow', 'lab')}>01 · 내 원칙</span>
        <h1 className={cx('p-h1')}>
          원칙은 문서가 아니라 <em>스위치다.</em>
        </h1>
        <p className={cx('lede')}>
          아래 다이얼을 직접 움직여 보세요. 값 하나를 바꾸면 같은 매수 후보의 판정이 ALLOW에서
          BLOCK으로 실제로 뒤집힙니다. 그게 이 시스템이 하는 일의 전부입니다.
        </p>

        <div className={cx('play')}>
          <div>
            <div className={cx('presets')} role="radiogroup" aria-label="원칙 프리셋">
              {PRESET_LABELS.map(({ key, label }) => (
                <PresetChoice
                  key={key}
                  value={key}
                  label={label}
                  checked={presetKey === key}
                  onChoose={applyPreset}
                />
              ))}
            </div>

            <div className={cx('sl')}>
              <Dial
                label="일일 손실 한도"
                min={0.5}
                max={5}
                step={0.1}
                value={dials.loss}
                display={`${dials.loss.toFixed(1)}%`}
                scale={['0.5%', '5.0%']}
                onChange={(loss) => setDials((prev) => ({ ...prev, loss }))}
              />
              <Dial
                label="금 ETF/ETN 최대 비중"
                min={0}
                max={40}
                step={1}
                value={dials.gold}
                display={`${dials.gold}%`}
                scale={['0%', '40%']}
                onChange={(gold) => setDials((prev) => ({ ...prev, gold }))}
              />
              <Dial
                label="종목별 최대 보유 비중"
                min={0}
                max={40}
                step={1}
                value={dials.pos}
                display={`${dials.pos}%`}
                scale={['0%', '40%']}
                onChange={(pos) => setDials((prev) => ({ ...prev, pos }))}
              />
            </div>

            <div className={cx('fixed')}>
              <div className={cx('fixed__r')}>
                <span>손절 / 익절</span>
                <span>{fixed.sl}</span>
              </div>
              <div className={cx('fixed__r')}>
                <span>최대 보유 기간</span>
                <span>{fixed.hold}</span>
              </div>
              <div className={cx('fixed__r')}>
                <span>현재 일일 누적 손실</span>
                <span>−1.8%</span>
              </div>
              <div className={cx('fixed__r')}>
                <span>금 ETF 현재 비중</span>
                <span>12.0%</span>
              </div>
              <div className={cx('fixed__r')}>
                <span>삼성전자 현재 비중</span>
                <span>6.5%</span>
              </div>
              <p className={cx('fixed__n')}>
                아래 판정은 이 고정 시나리오 값으로만 계산합니다. 한도 대비 80% 이상이면 WARN,
                100%를 넘으면 BLOCK입니다.
              </p>
            </div>
          </div>

          <div>
            <span className={cx('eyebrow', 'lab')}>02 · 판정</span>
            <h2>심사 보드</h2>
            <p className={cx('note')} style={{ marginBottom: '1.5rem' }}>
              모델이 낸 매수 후보 셋을 위 다이얼 값으로 검증한 결과입니다.
            </p>
            <div className={cx('judge')}>
              <div className={cx('judge__h')}>
                <span className={cx('lab')}>후보 · 신호 · 걸린 규칙</span>
                <span className={cx('lab')}>판정</span>
              </div>
              {rows.map((row) => (
                <div key={row.code} className={cx('judge__r')} data-v={row.verdict}>
                  <span>
                    <span className={cx('judge__n')}>{row.name}</span>
                    <span className={cx('judge__s')}>{row.detail}</span>
                  </span>
                  <span className={cx('chip')} data-v={row.verdict}>
                    {row.verdict}
                  </span>
                </div>
              ))}
              <div className={cx('judge__f')}>
                <span className={cx('lab')}>차단된 주문</span>
                <span className={cx('judge__c')}>{blocked}건 / {rows.length}건</span>
              </div>
            </div>
            <p className={cx('after')} style={{ color: 'var(--acc)' }} aria-live="polite">
              {line}
            </p>
          </div>
        </div>

        <div className={cx('p-sect')}>
          <span className={cx('eyebrow', 'lab')}>03 · 실측</span>
          <h2>원칙을 얹으면 결과가 바뀌는가</h2>
          <p className={cx('note')}>
            같은 모델 신호 위에서 원칙 개입 정도만 다르게 두고 13개 거래세션(2026.08.18–09.03)에
            적용했습니다. Baseline은 원칙 없이, Guide는 경고만 반영, Strict는 중대한 위반 시 주문을
            막습니다.
          </p>
          <div className={cx('scroller')}>
            <table>
              <thead>
                <tr>
                  <th scope="col">항목</th>
                  <th scope="col">Baseline</th>
                  <th scope="col">Guide</th>
                  <th scope="col" className={cx('col-s')}>
                    Strict
                  </th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td>비용반영 수익률</td>
                  <td>−0.55%</td>
                  <td>+0.03%</td>
                  <td className={cx('col-s')}>−0.23%</td>
                </tr>
                <tr>
                  <td>MDD</td>
                  <td>−0.90%</td>
                  <td>−0.40%</td>
                  <td className={cx('col-s')}>−0.36%</td>
                </tr>
                <tr>
                  <td>일간 CVaR (95%)</td>
                  <td>−0.66%</td>
                  <td>−0.35%</td>
                  <td className={cx('col-s')}>−0.28%</td>
                </tr>
                <tr>
                  <td>거래 수</td>
                  <td>14</td>
                  <td>8</td>
                  <td className={cx('col-s')}>6</td>
                </tr>
                <tr>
                  <td>차단한 원칙 위반</td>
                  <td>—</td>
                  <td>—</td>
                  <td className={cx('col-s')}>2건</td>
                </tr>
              </tbody>
            </table>
          </div>
          <p className={cx('after')}>
            13세션은 짧은 구간이라 절대적인 성과 우열을 단정하지 않습니다. 다만 원칙을 얹었을 때
            MDD와 CVaR이 함께 개선되는 방향으로 움직였고, Strict에서 RiskEngine이 실제로 2건을
            걸러냈다는 사실은 이번 실행에서 확인됐습니다.
          </p>
        </div>

        <div className={cx('p-sect')}>
          <span className={cx('eyebrow', 'lab')}>04 · 권한</span>
          <h2>AI에게 준 것과 주지 않은 것</h2>
          <p className={cx('note')}>
            더 많은 권한을 줄 수도 있었지만 검토 끝에 스스로 좁혔습니다. 예측력보다 통제 가능성을
            앞에 두었습니다.
          </p>
          <div className={cx('pow')}>
            <div>
              <span className={cx('lab')}>CAN</span>
              <ul>
                <li>후보 순위를 다시 매긴다</li>
                <li>매수를 거부한다</li>
              </ul>
            </div>
            <div className={cx('pow--no')}>
              <span className={cx('lab')}>CANNOT</span>
              <ul>
                <li>주문 수량을 정한다</li>
                <li>수량을 늘리거나 줄인다</li>
                <li>없던 후보를 새로 만든다</li>
              </ul>
            </div>
          </div>
          <ScrollCue flow />
        </div>
      </div>
    </section>
  );
}

function PresetChoice({
  value,
  label,
  checked,
  onChoose,
}: {
  value: PresetKey;
  label: string;
  checked: boolean;
  onChoose: (value: PresetKey) => void;
}) {
  const id = useId();
  return (
    <>
      <input
        type="radio"
        name="intro-preset"
        id={id}
        value={value}
        checked={checked}
        onChange={() => onChoose(value)}
      />
      <label htmlFor={id}>{label}</label>
    </>
  );
}

function Dial({
  label,
  min,
  max,
  step,
  value,
  display,
  scale,
  onChange,
}: {
  label: string;
  min: number;
  max: number;
  step: number;
  value: number;
  display: string;
  scale: [string, string];
  onChange: (value: number) => void;
}) {
  const id = useId();
  return (
    <div>
      <div className={cx('sl__h')}>
        <label className={cx('lab')} htmlFor={id}>
          {label}
        </label>
        <span className={cx('sl__v')}>{display}</span>
      </div>
      <input
        type="range"
        id={id}
        min={min}
        max={max}
        step={step}
        value={value}
        style={{ ['--fill' as string]: fillPercent(value, min, max) }}
        onChange={(event) => onChange(Number(event.target.value))}
      />
      <div className={cx('sl__sc')}>
        <span>{scale[0]}</span>
        <span>{scale[1]}</span>
      </div>
    </div>
  );
}
