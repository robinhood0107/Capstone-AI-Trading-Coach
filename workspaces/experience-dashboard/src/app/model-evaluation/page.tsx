import { PageHeader } from '@/shared/ui/Panel';
import { ModelEvaluationView } from '@/features/model-evaluation/ModelEvaluationView';

export default function Page() {
  return (
    <div className="space-y-8">
      <PageHeader
        eyebrow="Signal v2"
        title="모델 비교"
        description="규칙 baseline, LSTM, LightGBM이 같은 조건에서 무엇을 말하는지 나란히 봅니다. 근거를 내지 못한 모델은 값 대신 ABSTAIN으로 남습니다."
      />
      <ModelEvaluationView defaultRunId="demo_s8_fake_e2e_0001" />
    </div>
  );
}
