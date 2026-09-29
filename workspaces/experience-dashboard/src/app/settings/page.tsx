import { SettingsPageContent } from '@/features/system/SettingsPageContent';
import { OwnerVertexCredentialView } from '@/features/strong-llm/OwnerVertexCredentialView';
import { MockCredentialView } from '@/features/brokerage/MockCredentialView';
import { AccountLoginSettings } from '@/features/account/AccountLoginSettings';

export default function Page() {
  return (
    <SettingsPageContent description="내 KIS 모의계좌를 연결하고, 자동매매 AI 검토와 투자 화면의 설명 방식을 정합니다.">
      <AccountLoginSettings />
      <MockCredentialView />
      <OwnerVertexCredentialView />
    </SettingsPageContent>
  );
}
