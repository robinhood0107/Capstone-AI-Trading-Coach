import type { DemoAgentConfig } from './config';
import { usageSnapshot } from './store';
import { marketPhaseAt } from './clock';
import {
  backtestData,
  calendarMetadata,
  projectBacktest,
  projectShowcase,
  scenarioAssumptions,
  scenarioSeedVersion,
  showcaseMetadata,
  sourceMetadata,
  visibleBars,
  visibleShowcaseEvents,
} from './ledger';
import type { DemoOverlay } from '../shared/contracts';

function benchmarkReturn(asOfDate: string, bars: ReturnType<typeof visibleBars>): { ticker: string; returnBps: number } | null {
  const values = bars.filter((bar) => bar.symbol === '^KS11').sort((left, right) => left.date.localeCompare(right.date));
  if (values.length < 2) return null;
  const first = values[0].close;
  const latest = values[values.length - 1].close;
  return { ticker: '^KS11', returnBps: Math.floor(((latest / first - 1) * 10_000) + 0.5) };
}

export function buildDemoState(sessionHash: string, overlay: DemoOverlay, config: DemoAgentConfig, now = new Date()) {
  const clock = marketPhaseAt(now);
  const source = sourceMetadata();
  const retrospectiveAvailable = clock.dateKst >= source.scenarioRange.end;
  const bars = visibleBars(clock.dateKst);
  const baseEvents = visibleShowcaseEvents(now, retrospectiveAvailable);
  const projection = projectShowcase(baseEvents, overlay, now, bars);
  const backtest = projectBacktest(clock.dateKst);
  const metadata = showcaseMetadata();
  const backtestMetadata = backtestData();
  const benchmark = retrospectiveAvailable ? benchmarkReturn(clock.dateKst, bars) : null;
  return {
    source: {
      seedVersion: scenarioSeedVersion(),
      provider: source.provider,
      sourceSha256: source.sourceSha256,
      sourceRange: source.scenarioRange,
      latestVisibleDate: projection.latestVisibleDate,
      retrospectiveAvailable,
    },
    clock: {
      ...clock,
      sourceScenarioDate: retrospectiveAvailable ? source.scenarioRange.start : null,
    },
    showcase: retrospectiveAvailable
      ? {
          label: metadata.label,
          selectionRule: metadata.selectionRule,
          selectedSymbols: metadata.selectedSymbols,
          initialCapital: metadata.initialCapital,
          cash: projection.cash,
          equity: projection.equity,
          returnBps: projection.returnBps,
          realizedPnl: projection.realizedPnl,
          unrealizedPnl: projection.unrealizedPnl,
          dividendCash: projection.dividendCash,
          positions: projection.positions,
          daily: projection.daily,
          events: projection.events,
          orders: projection.orders,
          benchmark,
        }
      : null,
    backtest,
    bars,
    overlay,
    usage: usageSnapshot(sessionHash, config, now),
    assumptions: scenarioAssumptions(),
    calendar: calendarMetadata(),
    dataSource: source,
    backtestDefinition: {
      id: backtestMetadata.id,
      label: backtestMetadata.label,
      testRange: backtestMetadata.testRange,
      signal: backtestMetadata.signal,
      execution: backtestMetadata.execution,
      universeLimitation: backtestMetadata.universeLimitation,
      selectionMethod: backtestMetadata.selectionMethod,
      usesFutureData: false,
    },
  };
}
