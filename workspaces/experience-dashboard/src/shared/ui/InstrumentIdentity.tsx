import type { InstrumentDisplayItem } from '@/shared/api/wire';

export function instrumentMap(items: InstrumentDisplayItem[]): Map<string, InstrumentDisplayItem> {
  return new Map(items.map((item) => [item.symbol, item]));
}

export function InstrumentIdentity({
  symbol,
  instrument,
  compact = false,
}: {
  symbol: string;
  instrument?: InstrumentDisplayItem;
  compact?: boolean;
}) {
  return (
    <span className="inline-flex min-w-0 items-center gap-2">
      <span
        aria-hidden
        className={`${compact ? 'h-7 w-7 text-[9px]' : 'h-9 w-9 text-[10px]'} grid shrink-0 place-items-center rounded-full font-bold text-white`}
        style={{ backgroundColor: instrument?.brandColor ?? '#64748B' }}
      >
        {instrument?.logoText ?? symbol.slice(0, 2)}
      </span>
      <span className="min-w-0">
        <span className="block truncate font-semibold text-ink">{instrument?.nameKo ?? '종목 정보 없음'}</span>
        <span className="tnum block text-[11px] text-faint">{symbol}</span>
      </span>
    </span>
  );
}
