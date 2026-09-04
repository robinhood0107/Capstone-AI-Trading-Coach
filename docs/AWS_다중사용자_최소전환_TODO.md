# AWS 다중 사용자 최소 전환 TODO

현재 `compose.ec2.yml`은 한 사용자와 한 KIS Mock 계정만을 위한 단일 EC2 배포다. 다중 사용자 SaaS가 아니다.

다중 사용자로 전환할 때 추가할 것은 세 가지뿐이다.

1. demo login을 Cognito 또는 동등한 관리형 인증으로 바꾸고 HttpOnly·Secure·SameSite cookie를 사용한다.
2. 사용자별 KIS credential을 KMS envelope encryption으로 암호화하고 복호화는 해당 사용자의 worker 실행 중에만 허용한다.
3. owner별 scheduler lease와 계좌별 동시 실행 잠금을 두어 한 사용자의 실패·쿼터·주문이 다른 사용자에게 번지지 않게 한다.

기존 사용자·원칙·판정·RAG history의 `user_id` 경계와 PostgreSQL RLS를 그대로 사용한다. 새 agent framework, 별도 Kafka 의존성, 마이크로서비스 분해는 이 전환의 조건이 아니다. AI provider 비용은 서비스가 제공하지 않고 사용자 key를 받는 정책으로 시작하며, 공용 유료 API 제공은 별도 제품 결정으로 남긴다.
