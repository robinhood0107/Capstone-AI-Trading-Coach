# AWS EC2 단일 사용자 배포

`deploy/p1/compose.ec2.yml`은 기존 P1 Compose에 붙이는 최소 override다. 한 EC2, 한 사용자, 한 KIS Mock 계정만 지원한다.

## 네트워크

- ALB security group만 EC2의 dashboard port에 접근한다.
- dashboard만 host port를 연다.
- Decision API, PostgreSQL, Redis, gRPC는 Docker network 안에 남긴다.
- ALB health check path는 `/healthz`다.

## 저장소와 비밀

EBS를 `/srv/capstone-p1`에 mount하고 `postgres`, `redis` 디렉터리를 만든다. Docker volume은 이 경로를 bind한다. backup은 기존 `./capstone backup`과 `./capstone restore` 검증 절차를 사용하고 snapshot은 보조 수단으로만 둔다.

Secrets Manager의 SecretString은 `{ "spring.env": "base64...", ... }` 모양으로 저장한다. key는 파일명이고 value는 파일 전체의 base64다. `ec2-secrets-bootstrap.sh`가 부팅 때 KMS로 복호화된 응답을 받아 0600 파일로 설치한다. secret 값은 command line, image, Git, 로그, UI에 넣지 않는다.

## systemd

1. 저장소를 `/opt/capstone`에 배치한다.
2. `/etc/capstone-p1/ec2.env`에 secret ARN, image tag, UID/GID, port 같은 비밀이 아닌 배포값을 둔다.
3. `deploy/p1/capstone-p1-ec2.service`를 `/etc/systemd/system/`에 설치한다.
4. `systemctl daemon-reload && systemctl enable --now capstone-p1-ec2`로 부팅 재기동을 건다.

실제 AWS resource 생성, 과금, domain, certificate, ALB 연결은 이 저장소 변경에 포함하지 않는다.
