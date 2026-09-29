import { NextRequest, NextResponse } from 'next/server';

/** Product paths are checked before Next's same-origin API rewrite reaches Spring. */
export function middleware(request: NextRequest) {
  const path = request.nextUrl.pathname;
  if (path.startsWith('/api/v1/demo/')) {
    return new NextResponse(null, { status: 404 });
  }
  return NextResponse.next();
}

export const config = {
  // Keep legacy demonstration endpoints closed on FULL. The standalone DEMO
  // application owns its own routes, cookies, quotas, and API boundary.
  matcher: '/((?!_next/|healthz|fonts/|fonts.css|ui-fonts.css|mascot.png|icon.svg|favicon.ico).*)',
};
