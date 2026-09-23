import { PageHeader } from '@/shared/ui/Panel';
import { OverviewView } from '@/features/overview/OverviewView';
import { DemoAgentView } from '@/features/demo/DemoAgentView';

export default function Page() {
  if (process.env.NEXT_PUBLIC_MARS_PRODUCT === 'demo') return <DemoAgentView />;
  return (
    <div className="space-y-8">
      <PageHeader
        eyebrow="Overview"
        title="오늘 상태"
        description="지금 계좌가 어떤 상태이고, 자동주문이 켜져 있는지 한눈에 봅니다."
      />
      <OverviewView />
    </div>
  );
}
