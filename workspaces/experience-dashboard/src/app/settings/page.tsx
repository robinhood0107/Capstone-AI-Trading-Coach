import { PageHeader } from '@/shared/ui/Panel';
import { StrongLlmSettingsView } from '@/features/strong-llm/StrongLlmSettingsView';
import { OwnerVertexCredentialView } from '@/features/strong-llm/OwnerVertexCredentialView';
import { SystemHealthView } from '@/features/system/SystemHealthView';
import { ExplainModeSettings } from '@/features/system/ExplainModeSettings';
import { MockCredentialView } from '@/features/brokerage/MockCredentialView';
import { AccountLoginSettings } from '@/features/account/AccountLoginSettings';

export default function Page() {
  const fullProduct = process.env.NEXT_PUBLIC_MARS_PRODUCT === 'full';
  return (
    <div className="space-y-8">
      <PageHeader
        eyebrow="설정"
        title="설정"
        description={
          fullProduct
            ? '내 KIS 모의계좌를 연결하고, 자동매매 AI 검토와 투자 화면의 설명 방식을 정합니다.'
            : '화면을 어떻게 읽을지, 판단과 설명에 어떤 모델을 쓸지 정합니다.'
        }
      />
      {/* 읽기 설정을 맨 위에 둔다. 모델 설정보다 먼저 만나는 것이 자연스럽고,
          용어를 모르는 사람일수록 이 화면 아래쪽까지 내려가지 않는다. */}
      <ExplainModeSettings />
      <AccountLoginSettings />
      {/* FULL 은 KIS 모의계좌와 사용자 자기 Vertex 서비스 계정(자동매매 AI 검토)을 함께 받는다. */}
      {fullProduct ? (
        <>
          <MockCredentialView />
          <OwnerVertexCredentialView />
        </>
      ) : (
        <StrongLlmSettingsView />
      )}
      <SystemHealthView />
    </div>
  );
}
