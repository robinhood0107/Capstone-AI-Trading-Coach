import { PageHeader } from '@/shared/ui/Panel';
import { OrderReviewView } from '@/features/order-review/OrderReviewView';

export default function Page() {
  return (
    <div className="space-y-8">
      <PageHeader
        eyebrow="Decision · Risk"
        title="주문 검토"
        description="이 주문을 내도 되는지, 어떤 근거로 그렇게 판정했는지 봅니다. 판정은 이 화면이 아니라 Decision Platform이 만듭니다."
      />
      <OrderReviewView />
    </div>
  );
}
