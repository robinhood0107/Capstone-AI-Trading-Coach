import { cx } from './cx';
import { ScrollCue } from './ScrollCue';

/** 밝게 1막 — Tally. 숫자보다 질문을 먼저 던지고, 8+6=14라는 답은 lede에서 밝힌다. */
export function LightAct1() {
  return (
    <section className={cx('act', 'act--1')}>
        <div className={cx('t1', 'wrap')}>
          <div className={cx('t1__grid')}>
            <div>
              <span className={cx('t-pill')}><i aria-hidden="true"></i><span className={cx('lab')} style={{ letterSpacing: '.07em' }}>KIS 계좌 자동매매 · 원칙 검증 후 주문</span></span>
              <h1 className={cx('p-h1')}>이 주문, 내도 되는 걸까?</h1>
              <p className={cx('lede')}>AI가 아니라 열네 개의 규칙이 답합니다. 8개는 당신이 정한 원칙, 6개는 시스템이 계산하는 위험 규칙입니다. LSTM은 후보만 낼 뿐, 최종 통과 여부는 원칙이 정합니다.</p>
              <div className={cx('cta')}>
                <a className={cx('btn', 'btn--p')} href="#introEnd">대시보드 바로 보기</a>
                <a className={cx('btn', 'btn--g')} href="#light2">어떻게 막는지 보기</a>
              </div>
              <p className={cx('fine', 'lab')}><span>규칙 14개</span><span>프리셋 3종</span><span>종목 31 + 금 ETF/ETN</span><span>AI 권한 2개</span></p>
            </div>

            <div>
              <div className={cx('t-card')}>
                <div className={cx('t-card__h')}><span className={cx('lab')} style={{ letterSpacing: '.07em' }}>000660 SK하이닉스 · BUY</span><span className={cx('lab')} style={{ color: 'var(--acc)' }}>STRICT</span></div>
                <div className={cx('t-card__b')}>
                  <div className={cx('t-row')}><span>종목별 최대 보유 비중</span><span className={cx('v', 'v--pass')}>PASS</span></div>
                  <div className={cx('t-row')}><span>1회 최대 주문 금액</span><span className={cx('v', 'v--pass')}>PASS</span></div>
                  <div className={cx('t-row')}><span>고변동성 가드</span><span className={cx('v', 'v--warn')}>WARN</span></div>
                  <div className={cx('t-row')}><span>일일 최대 주문 횟수</span><span className={cx('v', 'v--pass')}>PASS</span></div>
                  <div className={cx('t-row')}><span>일일 손실 한도</span><span className={cx('v', 'v--block')}>BLOCK</span></div>
                  <div className={cx('t-card__f')}><span className={cx('lab')}>최종 판정</span><span className={cx('t-card__v')}>주문 차단됨</span></div>
                </div>
              </div>
            </div>
          </div>

          <div className={cx('t-stats')}>
            <div className={cx('t-st')}><span className={cx('t-st__n')}>2</span><span className={cx('t-st__k', 'lab')}>BLOCKED VIOLATIONS</span><p className={cx('t-st__d')}>실계좌와 같은 경로로 돌린 운용에서 RiskEngine이 실제로 걸러낸 원칙 위반 주문.</p></div>
            <div className={cx('t-st')}><span className={cx('t-st__n')}>2</span><span className={cx('t-st__k', 'lab')}>AI POWERS</span><p className={cx('t-st__d')}>재순위 조정과 매수 거부. 수량 관련 필드는 판단 결과에 존재하지 않습니다.</p></div>
            <div className={cx('t-st')}><span className={cx('t-st__n')}>21</span><span className={cx('t-st__k', 'lab')}>YEARS WALK-FORWARD</span><p className={cx('t-st__d')}>2005–2026 구간을 걸어가며 검증한 백테스트 범위.</p></div>
          </div>
        </div>

        <ScrollCue />
      </section>
  );
}
