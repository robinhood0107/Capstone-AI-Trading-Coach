import { StrategyView } from '@/features/strategy/StrategyView';
import { modelNote, reportNote } from '@demo/client/strategy-copy';

export default function DemoStrategyPage() {
  return <StrategyView defaultTab="model" reportNote={reportNote} modelNote={modelNote} />;
}
