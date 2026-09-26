import { NextRequest, NextResponse } from 'next/server';

/** Product paths are checked before Next's same-origin API rewrite reaches Spring. */
export function middleware(request: NextRequest) {
  const product = process.env.NEXT_PUBLIC_MARS_PRODUCT;
  const path = request.nextUrl.pathname;
  if (product === 'demo') {
    const allowedPage = request.method === 'GET' && path === '/';
    const allowedApi = request.method === 'POST' && path === '/api/v1/demo/agent/ask';
    return allowedPage || allowedApi ? NextResponse.next() : new NextResponse(null, { status: 404 });
  }
  if (product === 'full' && path.startsWith('/api/v1/demo/')) {
    return new NextResponse(null, { status: 404 });
  }
  return NextResponse.next();
}

export const config = {
  // Only known static assets and liveness bypass the product gate. New page/API
  // paths are denied by default in DEMO, even if they contain a dot.
  matcher: '/((?!_next/|healthz|fonts/|fonts.css|ui-fonts.css|mascot.png|icon.svg|favicon.ico).*)',
};
