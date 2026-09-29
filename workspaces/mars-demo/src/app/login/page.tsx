import { redirect } from 'next/navigation';
import { currentDemoSession } from '@demo/server/session-server';

export const dynamic = 'force-dynamic';

export default async function LoginPage() {
  await currentDemoSession();
  redirect('/');
}
