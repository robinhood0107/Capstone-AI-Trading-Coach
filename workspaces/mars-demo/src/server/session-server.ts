import { cookies } from 'next/headers';
import { DEMO_SESSION_COOKIE, verifyDemoSession } from './session';

export async function currentDemoSession() {
  const cookieStore = await cookies();
  return verifyDemoSession(cookieStore.get(DEMO_SESSION_COOKIE)?.value);
}
