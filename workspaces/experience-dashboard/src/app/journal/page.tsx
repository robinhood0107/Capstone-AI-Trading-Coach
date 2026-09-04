import { JournalView } from '@/features/journal/JournalView';
import { PageHeader } from '@/shared/ui/Panel';

export default function JournalPage() {
  return (
    <div className="space-y-8">
      <PageHeader
        eyebrow="Learning Journal"
        title="학습일지"
        description="투자 판단과 새로 배운 내용을 내 계정에 기록합니다."
      />
      <JournalView />
    </div>
  );
}
