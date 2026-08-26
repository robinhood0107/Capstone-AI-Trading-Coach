import { NextResponse } from 'next/server';

import { loadTeamBPreviewArtifact } from '@/shared/server/teamBArtifact';

export const dynamic = 'force-dynamic';

export async function GET() {
  try {
    const artifact = await loadTeamBPreviewArtifact();
    return NextResponse.json(artifact, {
      status: 200,
      headers: { 'Cache-Control': 'no-store' },
    });
  } catch {
    return NextResponse.json(
      { status: 'DOWN', code: 'TEAM_B_PREVIEW_UNAVAILABLE' },
      { status: 503, headers: { 'Cache-Control': 'no-store' } },
    );
  }
}
