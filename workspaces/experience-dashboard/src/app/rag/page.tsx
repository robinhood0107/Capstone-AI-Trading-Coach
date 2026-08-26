import { PageHeader } from '@/shared/ui/Panel';
import { RagGuideView } from '@/features/rag-source/RagGuideView';

export default function Page() {
  return (
    <div className="space-y-8">
      <PageHeader
        eyebrow="RAG"
        title="금융 가이드"
        description="개념과 위험을 출처와 함께 설명합니다. 무엇을 사고 팔지는 답하지 않고, 이 답변은 주문 판정에 영향을 주지 않습니다."
      />
      <RagGuideView />
    </div>
  );
}
