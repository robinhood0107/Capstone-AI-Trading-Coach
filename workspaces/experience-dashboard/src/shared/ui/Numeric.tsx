interface NumericProps {
  value: number | null;
  format: (value: number) => string;
  /** 값이 없을 때 사용자에게 알려줄 사유. "0"으로 대체하지 않는다. */
  missingReason?: string;
  className?: string;
}

/**
 * 결측 표기 규칙 (최종 명세서 8.4.1 / API 명세서 6.1):
 * producer나 row가 없는 값은 0·false로 꾸미지 않는다.
 * 화면에서는 해치 처리한 빈 슬롯으로 "자리는 있으나 근거가 없다"를 명시한다.
 */
export function Numeric({ value, format, missingReason, className = '' }: NumericProps) {
  if (value === null || !Number.isFinite(value)) {
    return (
      <span
        className={`hatch inline-flex items-center rounded-full border border-line px-2.5 py-0.5 text-[12px] text-faint ${className}`}
        title={missingReason ?? '검증된 근거가 없어 값을 표시하지 않습니다.'}
      >
        근거 없음
      </span>
    );
  }
  return <span className={`tnum ${className}`}>{format(value)}</span>;
}

export function Delta({ value, format }: { value: number | null; format: (v: number) => string }) {
  if (value === null || !Number.isFinite(value)) {
    return <Numeric value={null} format={format} />;
  }
  const tone = value > 0 ? 'text-allow' : value < 0 ? 'text-block' : 'text-muted';
  return <span className={`tnum font-semibold ${tone}`}>{format(value)}</span>;
}
