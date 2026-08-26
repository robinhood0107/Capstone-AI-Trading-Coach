import { PageHeader } from '@/shared/ui/Panel';
import { PrinciplesView } from '@/features/principles/PrinciplesView';

export default function Page() {
  return (
    <div className="space-y-8">
      <PageHeader
        eyebrow="Principle"
        title="내 투자 원칙"
        description="여기서 정한 기준이 주문 검토와 백테스트에 똑같이 적용됩니다. 값을 바꾸면 새 버전으로 저장됩니다."
      />
      <PrinciplesView />
    </div>
  );
}
