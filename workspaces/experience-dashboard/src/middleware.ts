import { NextResponse } from 'next/server';

/** Deny the password route before Next's same-origin API rewrite reaches Spring. */
export function middleware() {
  return new NextResponse(null, { status: 404 });
}

export const config = {
  matcher: '/api/v1/auth/login',
};
