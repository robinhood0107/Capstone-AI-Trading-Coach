import { PageHeader } from '@/shared/ui/Panel';
import { OverviewView } from '@/features/overview/OverviewView';

export default function Page() {
  return (
    <div className="space-y-8">
      <PageHeader
        eyebrow="Overview"
        title="오늘 상태"
        description="지금 계좌가 어떤 상태이고, 자동주문이 켜져 있는지 먼저 확인합니다. 근거가 없는 값은 비워 둡니다."
      />
      <OverviewView />
    </div>
  );
}
