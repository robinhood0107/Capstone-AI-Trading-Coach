import { PageHeader } from '@/shared/ui/Panel';
import { BacktestReportView } from '@/features/backtest-report/BacktestReportView';

export default function Page() {
  return (
    <div className="space-y-8">
      <PageHeader
        eyebrow="Backtest"
        title="백테스트 리포트"
        description="원칙과 안전장치를 켰을 때 수익률·낙폭·원칙 위반이 어떻게 달라지는지 비교합니다."
      />
      <BacktestReportView defaultRunId="demo_s8_fake_e2e_0001" />
    </div>
  );
}
