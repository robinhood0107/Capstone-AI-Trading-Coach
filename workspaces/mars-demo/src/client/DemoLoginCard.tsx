import { LoginCardFrame } from '@/shared/ui/LoginCardFrame';
import { DemoLoginButton } from './DemoLoginButton';

export function DemoLoginCard() {
  return (
    <LoginCardFrame
      headingId="demo-login-title"
      title="로그인"
      description="투자 원칙과 운용 데이터를 계정에 안전하게 보관합니다."
      className="shadow-card"
    >
      <DemoLoginButton />
    </LoginCardFrame>
  );
}
