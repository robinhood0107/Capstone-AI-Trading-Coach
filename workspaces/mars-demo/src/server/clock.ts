import calendarData from '../../data/krx-calendar.v1.json';
import type { DemoMarketPhase } from '../shared/contracts';

interface CalendarSession {
  date: string;
  openKst: string;
  closeKst: string;
}

interface CalendarDocument {
  policyVersion: string;
  range: { start: string; end: string };
  sessions: CalendarSession[];
}

const calendar = calendarData as CalendarDocument;
const sessionDates = new Set(calendar.sessions.map((session) => session.date));

export interface DemoClock {
  utc: string;
  kst: string;
  dateKst: string;
  timeKst: string;
  phase: DemoMarketPhase;
  phaseLabel: string;
  isTradingSession: boolean;
  calendarPolicyVersion: string;
}

function kstParts(now: Date) {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Seoul',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hourCycle: 'h23',
  }).formatToParts(now);
  const get = (type: string) => parts.find((part) => part.type === type)?.value ?? '';
  const date = `${get('year')}-${get('month')}-${get('day')}`;
  const time = `${get('hour')}:${get('minute')}:${get('second')}`;
  return { date, time, hour: Number(get('hour')), minute: Number(get('minute')) };
}

function weekend(date: string): boolean {
  const [year, month, day] = date.split('-').map(Number);
  const weekday = new Date(Date.UTC(year, month - 1, day)).getUTCDay();
  return weekday === 0 || weekday === 6;
}

export function marketPhaseAt(now: Date): DemoClock {
  const { date, time, hour, minute } = kstParts(now);
  const insideRange = date >= calendar.range.start && date <= calendar.range.end;
  let phase: DemoMarketPhase;
  let phaseLabel: string;
  let isTradingSession = false;

  if (!insideRange) {
    phase = 'CALENDAR_UNAVAILABLE';
    phaseLabel = '달력 범위 밖 · 새 주문 없음';
  } else if (!sessionDates.has(date)) {
    phase = weekend(date) ? 'WEEKEND' : 'HOLIDAY';
    phaseLabel = weekend(date) ? '휴장 · 주말' : '휴장 · KRX 거래일 달력';
  } else {
    isTradingSession = true;
    const minutes = hour * 60 + minute;
    if (minutes < 8 * 60) {
      phase = 'NIGHT';
      phaseLabel = '야간 · 주식 체결 없음';
    } else if (minutes < 9 * 60) {
      phase = 'PREOPEN';
      phaseLabel = minutes < 8 * 60 + 30 ? '장전 · 개장 전' : '장전 · 주문 접수 시간';
    } else if (minutes < 15 * 60 + 30) {
      phase = 'INTRADAY';
      phaseLabel = '장중';
    } else if (minutes < 16 * 60) {
      phase = 'CLOSE';
      phaseLabel = '장마감';
    } else if (minutes < 20 * 60) {
      phase = 'AFTER_HOURS';
      phaseLabel = '시간외';
    } else {
      phase = 'NIGHT';
      phaseLabel = '야간 · 주식 체결 없음';
    }
  }

  const kst = `${date} ${time} KST`;
  return {
    utc: now.toISOString(),
    kst,
    dateKst: date,
    timeKst: time,
    phase,
    phaseLabel,
    isTradingSession,
    calendarPolicyVersion: calendar.policyVersion,
  };
}

export function visibleCalendarSessions(): CalendarSession[] {
  return calendar.sessions;
}
