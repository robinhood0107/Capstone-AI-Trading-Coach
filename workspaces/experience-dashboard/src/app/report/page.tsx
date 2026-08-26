import { PageHeader } from '@/shared/ui/Panel';
import { ReportView } from '@/features/report/ReportView';

export default function Page() {
  return (
    <div className="space-y-8">
      <PageHeader
        eyebrow="Report"
        title="보고서 캡처"
        description="중간보고서와 발표자료에 그대로 넣을 수 있게 핵심 화면을 한자리에 모았습니다."
      />
      <ReportView />
    </div>
  );
}
