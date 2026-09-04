interface NumericProps {
  value: number | null;
  format: (value: number) => string;
  missingReason?: string;
  className?: string;
}

/** Missing values stay distinct from numeric zero. */
export function Numeric({ value, format, missingReason, className = '' }: NumericProps) {
  if (value === null || !Number.isFinite(value)) {
    return (
      <span
        className={`hatch inline-flex items-center rounded-full border border-line px-2.5 py-0.5 text-[12px] text-faint ${className}`}
        title={missingReason ?? '검증된 근거가 없어 값을 표시하지 않습니다.'}
      >
        <span aria-hidden>—</span>
        <span className="sr-only">값 없음</span>
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
