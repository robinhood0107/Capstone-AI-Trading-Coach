/** @type {import('next').NextConfig} */
const nextConfig = {
  output: 'standalone',
  reactStrictMode: true,
  poweredByHeader: false,
  // RAG ask can spend up to 15s retrieving and 60s generating. The default
  // 30s rewrite proxy timeout would return 500 before the API completes.
  experimental: { proxyTimeout: 85_000 },
  async rewrites() {
    // Next serializes rewrites during image build. Compose runtime environment
    // cannot change the destination later: both public products name the API
    // service `api`, while the development stack uses `decision-platform`.
    const publicProduct = ['demo', 'full'].includes(process.env.NEXT_PUBLIC_MARS_PRODUCT ?? '');
    const decisionPlatform =
      process.env.DECISION_PLATFORM_INTERNAL_URL ??
      (publicProduct ? 'http://api:8080' : 'http://decision-platform:8080');
    return [{ source: '/api/:path*', destination: `${decisionPlatform}/api/:path*` }];
  },
  async headers() {
    // S8 보안 gate: 외부 REST 경계의 기본 security header.
    // dev 서버의 webpack HMR 번들은 eval() 기반이라 'unsafe-eval'이 없으면
    // 클라이언트 자바스크립트가 전혀 실행되지 않는다(버튼·슬라이더가 눌러도 반응 없음).
    // 프로덕션 빌드는 eval을 쓰지 않으므로 이 완화는 dev 서버에만 적용한다.
    const scriptSrc =
      process.env.NODE_ENV === 'production'
        ? "script-src 'self' 'unsafe-inline'"
        : "script-src 'self' 'unsafe-inline' 'unsafe-eval'";
    return [
      {
        source: '/:path*',
        headers: [
          { key: 'X-Content-Type-Options', value: 'nosniff' },
          { key: 'X-Frame-Options', value: 'DENY' },
          // no-referrer 는 같은 출처 POST 의 Origin 을 null 로 바꿔 API 의 CORS 검사에 걸린다.
          { key: 'Referrer-Policy', value: 'same-origin' },
          {
            key: 'Content-Security-Policy',
            value: `default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; ${scriptSrc}; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'`,
          },
        ],
      },
    ];
  },
};
export default nextConfig;
