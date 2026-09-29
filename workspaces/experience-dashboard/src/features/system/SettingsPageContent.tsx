import type { ReactNode } from 'react';
import { PageHeader } from '@/shared/ui/Panel';
import { SystemHealthView } from '@/features/system/SystemHealthView';
import { ExplainModeSettings } from '@/features/system/ExplainModeSettings';

/** Shared MARS settings layout; each product supplies only its owned settings panels. */
export function SettingsPageContent({
  description = '화면 설명과 서비스 상태를 확인합니다.',
  children,
}: {
  description?: string;
  children?: ReactNode;
} = {}) {
  return (
    <div className="space-y-8">
      <PageHeader
        eyebrow="설정"
        title="설정"
        description={description}
      />
      <ExplainModeSettings />
      {children}
      <SystemHealthView />
    </div>
  );
}
