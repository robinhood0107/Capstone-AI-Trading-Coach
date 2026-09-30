import { StrategyView } from '@/features/strategy/StrategyView';
import { modelNote, reportNote } from '@demo/client/strategy-copy';

export default function DemoBacktestPage() {
  return <StrategyView defaultTab="backtest" reportNote={reportNote} modelNote={modelNote} />;
}
