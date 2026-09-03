import { StrategyView } from '@/features/strategy/StrategyView';

/** 기존 링크와 보고서 캡처 동선을 유지하기 위한 deep link. 화면은 /strategy와 같다. */
export default function Page() {
  return <StrategyView defaultTab="model" defaultRunId="demo_s8_fake_e2e_0001" />;
}
