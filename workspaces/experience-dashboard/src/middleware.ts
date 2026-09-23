import { NextResponse } from 'next/server';

/** Deny the password route before Next's same-origin API rewrite reaches Spring. */
export function middleware() {
  if (process.env.NEXT_PUBLIC_MARS_PRODUCT !== 'full' && process.env.NEXT_PUBLIC_MARS_PRODUCT !== 'demo') {
    return NextResponse.next();
  }
  return new NextResponse(null, { status: 404 });
}

export const config = {
  matcher: '/api/v1/auth/login',
};
