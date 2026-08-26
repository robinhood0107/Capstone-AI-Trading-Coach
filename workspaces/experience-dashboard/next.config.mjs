/** @type {import('next').NextConfig} */
const nextConfig = {
  output: 'standalone',
  reactStrictMode: true,
  poweredByHeader: false,
  async rewrites() {
    const apiEdge = process.env.API_EDGE_INTERNAL_URL ?? 'http://api-edge:8080';
    return [{ source: '/api/:path*', destination: `${apiEdge}/api/:path*` }];
  },
  async headers() {
    // S8 보안 gate: 외부 REST 경계의 기본 security header.
    return [
      {
        source: '/:path*',
        headers: [
          { key: 'X-Content-Type-Options', value: 'nosniff' },
          { key: 'X-Frame-Options', value: 'DENY' },
          { key: 'Referrer-Policy', value: 'no-referrer' },
          {
            key: 'Content-Security-Policy',
            value:
              "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'",
          },
        ],
      },
    ];
  },
};
export default nextConfig;
