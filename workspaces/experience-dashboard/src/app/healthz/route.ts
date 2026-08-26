import { NextResponse } from 'next/server';

import { loadTeamBPreviewArtifact } from '@/shared/server/teamBArtifact';

export const dynamic = 'force-dynamic';

export async function GET() {
  try {
    await loadTeamBPreviewArtifact();
    return NextResponse.json(
      { status: 'UP', service: 'experience-dashboard', teamBPreview: 'LEGACY_RECEIVED_PREVIEW' },
      { status: 200, headers: { 'Cache-Control': 'no-store' } },
    );
  } catch {
    return NextResponse.json(
      { status: 'DOWN', service: 'experience-dashboard', teamBPreview: 'UNAVAILABLE' },
      { status: 503, headers: { 'Cache-Control': 'no-store' } },
    );
  }
}
