import { readFile } from 'node:fs/promises';

const artifactPath = process.env.TEAM_B_PREVIEW_ARTIFACT ?? '/artifacts/team-b/005930.KS.json';

type PreviewMetadata = {
  classification?: unknown;
  realTeamB?: unknown;
  teamBRealArtifactMissing?: unknown;
  providerCalls?: unknown;
};

export type TeamBPreviewArtifact = Record<string, unknown> & {
  _preview: PreviewMetadata;
};

/** read-only volume의 legacy preview를 검증하고 real Team B 결과와 섞이지 않게 한다. */
export async function loadTeamBPreviewArtifact(): Promise<TeamBPreviewArtifact> {
  const raw = await readFile(artifactPath, { encoding: 'utf8' });
  if (Buffer.byteLength(raw, 'utf8') > 1024 * 1024) {
    throw new Error('TEAM_B_PREVIEW_TOO_LARGE');
  }
  const parsed: unknown = JSON.parse(raw);
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
    throw new Error('TEAM_B_PREVIEW_INVALID');
  }
  const artifact = parsed as Record<string, unknown>;
  const preview = artifact._preview;
  if (!preview || typeof preview !== 'object' || Array.isArray(preview)) {
    throw new Error('TEAM_B_PREVIEW_METADATA_MISSING');
  }
  const metadata = preview as PreviewMetadata;
  if (
    metadata.classification !== 'LEGACY_RECEIVED_PREVIEW' ||
    metadata.realTeamB !== false ||
    metadata.teamBRealArtifactMissing !== true ||
    metadata.providerCalls !== 0
  ) {
    throw new Error('TEAM_B_PREVIEW_AUTHORITY_INVALID');
  }
  return artifact as TeamBPreviewArtifact;
}
