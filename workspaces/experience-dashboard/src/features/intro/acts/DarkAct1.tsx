import { cx } from './cx';
import { ScrollCue } from './ScrollCue';

/** 어둡게 1막 — Wayfare. 매스헤드와 출발안내판. */
export function DarkAct1() {
  return (
    <section className={cx('act', 'act--1')}>
        <div className={cx('w1')}>
          <header className={cx('mast')}>
            <span className={cx('mast__n')}>MAR<b>S</b></span>
            <span className={cx('mast__c', 'lab')}>국내주식 31 · 금 ETF/ETN · KIS 계좌 자동매매</span>
            <span className={cx('mast__l', 'lab')}><a href="#dark2">규칙</a><a href="#dark2">결과</a><a href="#introEnd">대시보드</a></span>
          </header>

          <div className={cx('w1__body', 'wrap')}>
            <p className={cx('w-eyebrow', 'lab')}><em>LIVE · 심사 보드</em><span>STRICT 시나리오</span><span>KIS 계좌 자동매매</span></p>
            <h1 className={cx('w-h1')}>후보는 AI가.<br /><span className={cx('hl')}>거부는 당신이.</span></h1>
            <div className={cx('w1__grid')}>
              <div>
                <p className={cx('lede')}>뉴스감성·LSTM·HMM이 매수 후보를 만듭니다. 그 다음 당신이 세운 원칙 8개와 시스템 규칙 6개가 후보를 한 줄씩 검증합니다. 통과하지 못한 주문은 나가지 않습니다.</p>
                <div className={cx('cta')}>
                  <a className={cx('btn', 'btn--p')} href="#introEnd">대시보드 바로 보기</a>
                  <a className={cx('btn', 'btn--g')} href="#dark2">원칙 만져보기</a>
                </div>
              </div>
              <div className={cx('stamp')}>
                <span className={cx('lab')} style={{ fontSize: '9.5px', letterSpacing: '.16em' }}>차단한 원칙 위반</span>
                <span className={cx('stamp__v')}>2건</span>
                <span className={cx('lab')} style={{ fontSize: '9.5px', letterSpacing: '.12em', color: 'var(--ink2)' }}>13 SESSIONS · STRICT</span>
              </div>
            </div>

            <div className={cx('scroller', 'board')}><table>
              <thead><tr><th scope="col">후보</th><th scope="col">신호</th><th scope="col">걸린 규칙</th><th scope="col">판정</th></tr></thead>
              <tbody>
                <tr><td><span className={cx('board__code')}>005930</span><span className={cx('board__name')}>삼성전자</span></td><td className={cx('lab')} style={{ textTransform: 'none' }}>BUY</td><td>—</td><td><span className={cx('chip', 'chip--a')}>ALLOW</span></td></tr>
                <tr><td><span className={cx('board__code')}>132030</span><span className={cx('board__name')}>KODEX 골드선물</span></td><td className={cx('lab')} style={{ textTransform: 'none' }}>BUY</td><td>금 ETF/ETN 최대 비중</td><td><span className={cx('chip', 'chip--w')}>WARN</span></td></tr>
                <tr><td><span className={cx('board__code')}>000660</span><span className={cx('board__name')}>SK하이닉스</span></td><td className={cx('lab')} style={{ textTransform: 'none' }}>BUY</td><td>일일 손실 한도</td><td><span className={cx('chip', 'chip--b')}>BLOCK</span></td></tr>
              </tbody>
            </table></div>
          </div>
        </div>

        <div className={cx('ticker')} aria-hidden="true"><div className={cx('ticker__in')}>
          <span><i></i><b>SESSIONS 13</b> 2026.08.18—09.03</span>
          <span><i></i><b>RULES 14</b> 사용자 8 · 시스템 6</span>
          <span><i></i><b>BLOCKED 2</b> STRICT 시나리오</span>
          <span><i></i><b>AI POWERS 2</b> 재순위 · 매수거부</span>
          <span><i></i><b>KIS 계좌</b> 자동매매 연동</span>
          <span><i></i><b>SESSIONS 13</b> 2026.08.18—09.03</span>
          <span><i></i><b>RULES 14</b> 사용자 8 · 시스템 6</span>
          <span><i></i><b>BLOCKED 2</b> STRICT 시나리오</span>
        </div></div>

        <ScrollCue />
      </section>
  );
}
