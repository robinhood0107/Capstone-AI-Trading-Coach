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

/**
 * 등락 표기. 국내 시장 관행(상승 적/하락 청)을 따르며 판정색(allow/block)과는
 * 다른 토큰(up/down)을 쓴다 — 색만으로 방향을 전달하지 않고 화살표를 같이 붙인다.
 */
export function Delta({
  value,
  format,
  missingReason,
  className = '',
}: {
  value: number | null;
  format: (v: number) => string;
  missingReason?: string;
  className?: string;
}) {
  if (value === null || !Number.isFinite(value)) {
    return <Numeric value={null} format={format} missingReason={missingReason} className={className} />;
  }
  const tone = value > 0 ? 'text-up' : value < 0 ? 'text-down' : 'text-muted';
  const arrow = value > 0 ? '↑' : value < 0 ? '↓' : '·';
  return (
    <span className={`tnum inline-flex items-baseline gap-1 font-semibold ${tone} ${className}`}>
      <span aria-hidden className="text-[9px]">
        {arrow}
      </span>
      {format(value)}
    </span>
  );
}
