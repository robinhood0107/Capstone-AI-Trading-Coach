import { cx } from './cx';
import { ScrollCue } from './ScrollCue';

/** 밝게 2막 — NAJM. 규칙이 어떻게 걸리는지 한 층씩 쌓아 보여준다. */
export function LightAct2() {
  return (
    <section className={cx('act', 'act--2')} id="light2">
        <div className={cx('marq')} aria-hidden="true"><div className={cx('marq__in')}>
          <span>원칙이 먼저다</span><i>★</i><span>AI는 후보만 낸다</span><i>★</i><span>통과 못 하면 주문은 없다</span><i>★</i>
          <span>원칙이 먼저다</span><i>★</i><span>AI는 후보만 낸다</span><i>★</i><span>통과 못 하면 주문은 없다</span><i>★</i>
        </div></div>

        <div className={cx('stack')}>
          <section className={cx('stack__i')}><div className={cx('stack__in')}>
            <span className={cx('stack__n')}>01</span>
            <div><h2>후보는 이렇게 만들어집니다</h2><p className={cx('stack__d')}>세 가지를 함께 씁니다. 어느 하나가 흔들려도 나머지가 잡아 주도록 서로 다른 성격의 신호를 겹쳐 두었습니다.</p></div>
            <ul className={cx('list')}>
              <li><span>LSTM — 가격·거래량으로 다음 구간의 방향을 예측</span><span>로그수익률</span></li>
              <li><span>규칙 Baseline — 이동평균·RSI로 잡는 기준점</span><span>벤치마크</span></li>
              <li><span>HMM 국면 필터 — 위험 선호 / 위험 회피 판별</span><span>애매하면 불신</span></li>
              <li><span>뉴스는 톤·볼륨 집계 지표만 사용</span><span>원문 미저장</span></li>
            </ul>
          </div></section>

          <section className={cx('stack__i')}><div className={cx('stack__in')}>
            <span className={cx('stack__n')}>02</span>
            <div><h2>열네 줄의 안전장치</h2><p className={cx('stack__d')}>위쪽 여덟 개는 당신이 값을 정합니다. 아래 여섯 개는 시스템이 계산해 관리합니다. 규칙 집합은 JSON 계약으로 고정되어 화면이나 코드가 바뀌어도 임의로 늘거나 줄지 않습니다.</p></div>
            <ul className={cx('list', 'list--split')}>
              <li><span>종목별 최대 보유 비중</span><span>사용자</span></li>
              <li><span>금 ETF/ETN 최대 비중</span><span>사용자</span></li>
              <li><span>1회 최대 주문 금액</span><span>사용자</span></li>
              <li><span>일일 손실 한도</span><span>사용자</span></li>
              <li><span>최대 낙폭(MDD) 한도</span><span>사용자</span></li>
              <li><span>일일 최대 주문 횟수</span><span>사용자</span></li>
              <li><span>부정적 뉴스 가드</span><span>사용자 · OFF</span></li>
              <li><span>공시 리스크 가드</span><span>사용자 · OFF</span></li>
              <li><span>고변동성 가드</span><span>시스템</span></li>
              <li><span>데이터 최신성 가드</span><span>시스템</span></li>
              <li><span>HMM 국면 가드</span><span>시스템</span></li>
              <li><span>평균회귀 경고</span><span>시스템</span></li>
              <li><span>ETF/ETN 상품위험 점검</span><span>시스템</span></li>
              <li><span>매수 여력 가드</span><span>시스템 · 예약</span></li>
            </ul>
          </div></section>

          <section className={cx('stack__i')}><div className={cx('stack__in')}>
            <span className={cx('stack__n')}>03</span>
            <div><h2>원칙을 얹으면 결과가 바뀌는가</h2><p className={cx('stack__d')}>같은 모델 신호 위에서 원칙 개입 정도만 다르게 두고, 같은 기간의 실제 거래 세션에 그대로 적용했습니다.</p></div>
            <div>
              <div className={cx('scroller')}><table>
                <thead><tr><th scope="col">항목</th><th scope="col">Baseline</th><th scope="col">Guide</th><th scope="col" className={cx('col-s')}>Strict</th></tr></thead>
                <tbody>
                  <tr><td>비용반영 수익률</td><td>−0.55%</td><td>+0.03%</td><td className={cx('col-s')}>−0.23%</td></tr>
                  <tr><td>MDD</td><td>−0.90%</td><td>−0.40%</td><td className={cx('col-s')}>−0.36%</td></tr>
                  <tr><td>일간 CVaR (95%)</td><td>−0.66%</td><td>−0.35%</td><td className={cx('col-s')}>−0.28%</td></tr>
                  <tr><td>거래 수</td><td>14</td><td>8</td><td className={cx('col-s')}>6</td></tr>
                  <tr><td>차단한 원칙 위반</td><td>—</td><td>—</td><td className={cx('col-s')}>2건</td></tr>
                </tbody>
              </table></div>
              <p className={cx('after')}>짧은 구간의 성과 우열은 단정하지 않습니다. 확인된 것은 둘입니다 — 원칙을 얹었을 때 MDD와 CVaR이 함께 개선되는 방향으로 움직였고, Strict에서 RiskEngine이 실제로 2건을 걸러냈습니다. 모델 자체의 검증 범위는 21년 walk-forward가 따로 맡습니다.</p>
            </div>
          </div></section>

          <section className={cx('stack__i')}><div className={cx('stack__in')}>
            <span className={cx('stack__n')}>04</span>
            <div><h2>AI에게 준 것과 주지 않은 것</h2><p className={cx('stack__d')}>더 많은 권한을 줄 수도 있었지만 검토 끝에 스스로 좁혔습니다. 예측력보다 통제 가능성을 앞에 두었습니다.</p></div>
            <div className={cx('pow')}>
              <div><span className={cx('lab')}>CAN</span><ul><li>후보 순위를 다시 매긴다</li><li>매수를 거부한다</li></ul></div>
              <div className={cx('pow--no')}><span className={cx('lab')}>CANNOT</span><ul><li>주문 수량을 정한다</li><li>수량을 늘리거나 줄인다</li><li>없던 후보를 새로 만든다</li></ul></div>
            </div>
          </div></section>
        </div>

        <div className={cx('letter')}>
          <div className={cx('letter__in')}>
            <h2>막은 주문도 전부 남깁니다.</h2>
            <p>자동매매에서 정말 중요한 건 무엇을 샀는지가 아니라 무엇을 사지 않았는지입니다. 원칙에 걸려 나가지 않은 주문도 어떤 규칙이 왜 막았는지까지 그대로 기록에 남습니다.</p>
            <p>후보 선정부터 주문·체결·대사·청산까지 하나의 폐루프가 돌아가고, 각 단계에서 원칙이 어떻게 개입했는지를 나중에 되짚을 수 있습니다. 그래야 다음 원칙을 더 낫게 고칠 수 있습니다.</p>
            <div className={cx('sign')}>
              <p className={cx('sign__n')}>잘 맞히는 것보다, 지킬 수 있는 것.</p>
              <p className={cx('sign__r')}>KIS 계좌 자동매매</p>
            </div>
            <ScrollCue flow style={{ color: 'oklch(90% .012 60)' }} />
          </div>
        </div>
      </section>
  );
}
