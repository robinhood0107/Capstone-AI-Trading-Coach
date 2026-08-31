import { PageHeader } from '@/shared/ui/Panel';
import { AutomationView } from '@/features/automation/AutomationView';

export default function AutomationPage() {
  return (
    <div className="space-y-8">
      <PageHeader
        eyebrow="Automation"
        title="자동운용 설정"
        description="KIS 모의계좌에서 사용할 최대 금액과 손절·익절 기준을 저장하고, 실제 시작 가능 상태를 확인합니다."
      />
      <AutomationView />
    </div>
  );
}
