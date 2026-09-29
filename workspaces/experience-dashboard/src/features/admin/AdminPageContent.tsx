import { PageHeader } from '@/shared/ui/Panel';
import { AdminConsole } from '@/features/admin/AdminConsole';

/** Shared MARS admin screen. Product routes may make its controls read-only. */
export function AdminPageContent({ readOnly = false }: { readOnly?: boolean } = {}) {
  return (
    <div className="space-y-8">
      <PageHeader
        eyebrow="관리"
        title="서비스 관리"
        description={readOnly
          ? '가입자와 자동운용 현황을 확인합니다.'
          : '가입자와 자동운용 현황을 보고, 가입·동시 자동운용 상한을 조정합니다.'}
      />
      <AdminConsole readOnly={readOnly} />
    </div>
  );
}
