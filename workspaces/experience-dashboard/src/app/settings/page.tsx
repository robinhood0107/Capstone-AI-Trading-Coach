import { PageHeader } from '@/shared/ui/Panel';
import { StrongLlmSettingsView } from '@/features/strong-llm/StrongLlmSettingsView';

export default function Page() {
  return (
    <div className="space-y-8">
      <PageHeader
        eyebrow="설정"
        title="Strong LLM"
        description="판단과 설명에 쓸 모델, 실패했을 때의 2차 모델, 답변 언어, 하루 호출 상한을 정합니다. API 키는 서버에서 암호화해 보관하며 저장 후에는 마지막 네 글자만 보입니다."
      />
      <StrongLlmSettingsView />
    </div>
  );
}
