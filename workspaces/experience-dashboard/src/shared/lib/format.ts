/**
 * 표기 규약 (API 명세서 3.0): 금액은 KRW 정수, 비율은 소수(3% = 0.03), 시각은 ISO-8601 + KST.
 * null은 여기서 문자열로 바뀌지 않는다. 호출부가 <Numeric>로 "근거 없음"을 그려야 한다.
 */

const KST = 'Asia/Seoul';

export function formatRatio(value: number, fractionDigits = 2): string {
  return `${(value * 100).toFixed(fractionDigits)}%`;
}

export function formatSignedRatio(value: number, fractionDigits = 2): string {
  const sign = value > 0 ? '+' : '';
  return `${sign}${(value * 100).toFixed(fractionDigits)}%`;
}

export function formatKrw(value: number): string {
  return `${new Intl.NumberFormat('ko-KR').format(Math.trunc(value))}원`;
}

export function formatCount(value: number): string {
  return new Intl.NumberFormat('ko-KR').format(value);
}

export function formatDecimal(value: number, fractionDigits = 2): string {
  return value.toFixed(fractionDigits);
}

export function formatKstDateTime(iso: string | null): string | null {
  if (!iso) return null;
  const parsed = Date.parse(iso);
  if (Number.isNaN(parsed)) return null;
  return new Intl.DateTimeFormat('ko-KR', {
    timeZone: KST,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(parsed);
}

export function formatKstDate(iso: string | null): string | null {
  if (!iso) return null;
  const parsed = Date.parse(iso);
  if (Number.isNaN(parsed)) return null;
  return new Intl.DateTimeFormat('ko-KR', {
    timeZone: KST,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).format(parsed);
}

export function relativeAge(iso: string | null): string | null {
  if (!iso) return null;
  const parsed = Date.parse(iso);
  if (Number.isNaN(parsed)) return null;
  const minutes = Math.floor((Date.now() - parsed) / 60_000);
  if (minutes < 1) return '방금';
  if (minutes < 60) return `${minutes}분 전`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}시간 전`;
  return `${Math.floor(hours / 24)}일 전`;
}
