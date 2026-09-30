import { createHash } from 'node:crypto';
import { barAt, showcaseMetadata } from './ledger';

function idFor(sessionHash: string, eventId: string): string {
  return `jrn_${createHash('sha256').update(`${sessionHash}:${eventId}`).digest('hex').slice(0, 32)}`;
}

export function initialJournalEntries(sessionHash: string, now = new Date()): Record<string, unknown>[] {
  const portfolio = showcaseMetadata();
  const orders = new Map(portfolio.events
    .filter((event) => event.type === 'ORDER_CREATED')
    .map((event) => [event.id, event]));
  const eventsByDate = new Map(portfolio.events
    .filter((event) => event.type === 'FILL' || event.type === 'NO_ACTION')
    .map((event) => [String(event.sourceDate), event]));
  return portfolio.dailyReceipt
    .map((day) => {
      const event = eventsByDate.get(day.date);
      if (!event) throw new Error(`DEMO journal source event missing for ${day.date}`);
      const isFill = event.type === 'FILL';
      const symbol = String(event.symbol);
      const sourceDate = day.date;
      const displayName = barAt(symbol, sourceDate)?.displayName ?? symbol.replace(/\.(KS|KQ)$/, '');
      const quantity = Number(event.quantity ?? 0);
      const amount = Number(event.grossAmount ?? 0);
      const commission = Number(event.commission ?? 0);
      const tax = Number(event.transactionTax ?? 0);
      const realizedPnl = Number(event.realizedPnl ?? 0);
      const orderId = isFill ? String(event.orderId) : null;
      const decisionId = orderId ? orders.get(orderId)?.decisionId : null;
      const action = event.side === 'SELL' ? '매도' : '매수';
      const outcome = isFill
        ? `${displayName} ${quantity}주 ${action}, 체결금액 ${amount.toLocaleString('ko-KR')}원. 수수료 ${commission.toLocaleString('ko-KR')}원${event.side === 'SELL' ? `, 거래세 ${tax.toLocaleString('ko-KR')}원, 비용 반영 실현손익 ${realizedPnl.toLocaleString('ko-KR')}원` : ''}. 기록된 일별 시세와 슬리피지 가정으로 계산했습니다.`
        : '이 날짜의 고정 거래 일정에는 신규 주문이 없었습니다. 가격 관측과 보유 종목 평가는 계속 기록했습니다.';
      const createdAt = `${sourceDate}T15:31:00+09:00`;
      return {
        journalId: idFor(sessionHash, event.id),
        title: isFill ? `${displayName} ${action} 복기 · ${sourceDate}` : `무주문 세션 복기 · ${sourceDate}`,
        content: `${outcome} 장마감 평가액 ${Number(day.equity).toLocaleString('ko-KR')}원, 현금 ${Number(day.cash).toLocaleString('ko-KR')}원, 보유 ${Object.keys(day.holdings).length}종목. 사후 구성 사례의 기록이며 이 날의 미래 가격을 사용한 판단 근거는 아닙니다.`,
        tags: [isFill ? '거래 복기' : '무주문 복기', '자동 생성'],
        links: {
          decisionId: typeof decisionId === 'string' ? decisionId : null,
          backtestRunId: null,
          ragAnswerId: null,
          orderId,
          automationRunId: `demo_run_${createHash('sha256').update(`${sessionHash}:${isFill ? orderId : event.id}`).digest('hex').slice(0, 24)}`,
        },
        version: 1,
        createdAt,
        updatedAt: createdAt,
      };
    })
    .filter((entry) => Date.parse(String(entry.createdAt)) <= now.getTime())
    .reverse();
}
