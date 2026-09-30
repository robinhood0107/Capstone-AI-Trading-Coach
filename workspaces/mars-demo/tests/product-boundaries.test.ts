import assert from 'node:assert/strict';
import { readdirSync, readFileSync } from 'node:fs';
import path from 'node:path';
import test from 'node:test';

const demoRoot = path.resolve(import.meta.dirname, '../src');
const fullRoot = path.resolve(import.meta.dirname, '../../experience-dashboard/src');

function sourceFiles(directory: string): string[] {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const filePath = path.join(directory, entry.name);
    if (entry.isDirectory()) return sourceFiles(filePath);
    return /\.(?:ts|tsx)$/.test(entry.name) ? [filePath] : [];
  });
}

function importsIn(source: string): string[] {
  const values: string[] = [];
  const expression = /(?:\bfrom\s*|\bimport\s*\()\s*['"]([^'"]+)['"]/g;
  for (const match of source.matchAll(expression)) values.push(match[1]);
  return values;
}

test('DEMO mounts the original FULL pages, shell, and common components', () => {
  const config = JSON.parse(readFileSync(path.resolve(demoRoot, '../tsconfig.json'), 'utf8')) as {
    compilerOptions: { paths: Record<string, string[]> };
  };
  assert.deepEqual(config.compilerOptions.paths['@/shared/api/client'], ['src/adapters/client.ts']);
  assert.deepEqual(config.compilerOptions.paths['@/shared/api/session'], ['src/adapters/session.tsx']);
  assert.deepEqual(config.compilerOptions.paths['@/features/*'], ['../experience-dashboard/src/features/*']);
  assert.deepEqual(config.compilerOptions.paths['@/shared/ui/*'], ['../experience-dashboard/src/shared/ui/*']);

  const shell = readFileSync(path.resolve(demoRoot, '../src/app/layout.tsx'), 'utf8');
  const sharedAppShell = readFileSync(path.resolve(fullRoot, 'shared/ui/AppShell.tsx'), 'utf8');
  assert.ok(shell.includes("import { AppShell } from '@/shared/ui/AppShell'"));
  assert.ok(shell.includes('<AppShell loginCard={<DemoLoginCard />}>'));
  assert.ok(sharedAppShell.includes('{loginCard}'));
  assert.ok(!sharedAppShell.includes("import { LoginCard }"));
  assert.ok(readFileSync(path.resolve(fullRoot, 'shared/api/endpoints.ts'), 'utf8').includes("from '@/shared/api/client'"));

  for (const route of ['page', 'principles/page', 'rag/page', 'report/page']) {
    const source = readFileSync(path.resolve(demoRoot, `../src/app/(workspace)/${route}.tsx`), 'utf8');
    assert.ok(source.includes('@full/app/'), `${route} should mount an existing FULL route`);
  }
  const automation = readFileSync(path.resolve(demoRoot, '../src/app/(workspace)/automation/page.tsx'), 'utf8');
  assert.ok(automation.includes("from '@/features/automation/AutomationView'"));
  assert.ok(automation.includes('runPageSize={40}'));
  for (const route of ['strategy/page', 'model-evaluation/page', 'backtest/page']) {
    const source = readFileSync(path.resolve(demoRoot, `../src/app/(workspace)/${route}.tsx`), 'utf8');
    assert.ok(source.includes("from '@/features/strategy/StrategyView'"));
  }
  for (const [route, component] of [['order-review/page', 'OrderReviewView'], ['journal/page', 'JournalView']]) {
    const source = readFileSync(path.resolve(demoRoot, `../src/app/(workspace)/${route}.tsx`), 'utf8');
    assert.ok(source.includes(`@/features/`));
    assert.ok(source.includes(`<${component} fillsFromDate="2026-08-18" />`));
  }
  assert.ok(!sourceFiles(demoRoot).some((filePath) => filePath.endsWith('DemoWorkspace.tsx')));
});

test('FULL source does not import DEMO adapters, Agent, or simulator modules', () => {
  const violations: string[] = [];
  for (const file of sourceFiles(fullRoot)) {
    const source = readFileSync(file, 'utf8');
    for (const specifier of importsIn(source)) {
      if (specifier.includes('mars-demo') || specifier.startsWith('@demo/') || specifier.includes('workspaces/mars-demo')) {
        violations.push(`${path.relative(fullRoot, file)} -> ${specifier}`);
      }
    }
  }
  assert.deepEqual(violations, []);
});

test('DEMO image packages the FULL presentation modules without FULL services or credential screens', () => {
  const dockerfile = readFileSync(path.resolve(demoRoot, '../Dockerfile'), 'utf8');
  for (const required of [
    'COPY workspaces/mars-demo ./workspaces/mars-demo',
    'src/shared/ui ./workspaces/experience-dashboard/src/shared/ui',
    'src/shared/lib ./workspaces/experience-dashboard/src/shared/lib',
    'src/shared/api/endpoints.ts',
    'src/shared/api/wire.ts',
    'src/features/overview ./workspaces/experience-dashboard/src/features/overview',
    'src/features/automation ./workspaces/experience-dashboard/src/features/automation',
    'src/features/order-review ./workspaces/experience-dashboard/src/features/order-review',
    'src/features/rag-source ./workspaces/experience-dashboard/src/features/rag-source',
    'public/ui-fonts.css ./workspaces/mars-demo/public/ui-fonts.css',
    'public/fonts ./workspaces/mars-demo/public/fonts',
    'public/mascot.png ./workspaces/mars-demo/public/mascot.png',
  ]) assert.ok(dockerfile.includes(required), `DEMO image should include ${required}`);
  for (const forbidden of [
    'workspaces/decision-platform', 'return-engine', 'private-reference',
    'src/features/brokerage', 'src/features/account', 'src/features/strong-llm',
    'src/shared/api/client.ts', 'src/shared/api/session.ts',
  ]) assert.ok(!dockerfile.includes(forbidden), `DEMO image must not include ${forbidden}`);
  const runner = dockerfile.split('FROM node:22-bookworm-slim AS runner')[1] ?? '';
  assert.ok(runner.includes('rm -rf /usr/local/lib/node_modules/npm /usr/local/bin/npm /usr/local/bin/npx'));
});

test('DEMO transport uses only its same-origin API adapter and signed visitor session', () => {
  const client = readFileSync(path.resolve(demoRoot, '../src/adapters/client.ts'), 'utf8');
  const session = readFileSync(path.resolve(demoRoot, '../src/adapters/session.tsx'), 'utf8');
  const adapter = readFileSync(path.resolve(demoRoot, '../src/server/full-ui-adapter.ts'), 'utf8');
  assert.ok(client.includes("credentials: 'same-origin'"));
  assert.ok(!client.includes('Authorization'));
  assert.ok(!session.includes('localStorage'));
  assert.ok(adapter.includes('applyOverlayAction'));
  assert.ok(adapter.includes('askVertex'));
  assert.ok(!adapter.includes('fetch('));

  const demoSettings = readFileSync(path.resolve(demoRoot, '../src/app/(workspace)/settings/page.tsx'), 'utf8');
  const sharedSettings = readFileSync(path.resolve(fullRoot, 'features/system/SettingsPageContent.tsx'), 'utf8');
  assert.ok(demoSettings.includes('SettingsPageContent'));
  assert.ok(!demoSettings.includes('MockCredentialView'));
  assert.ok(!demoSettings.includes('OwnerVertexCredentialView'));
  assert.ok(!demoSettings.includes('AccountLoginSettings'));
  assert.ok(!sharedSettings.includes("@/features/brokerage/"));
  assert.ok(!sharedSettings.includes("@/features/account/"));
  assert.ok(!sharedSettings.includes("@/features/strong-llm/"));
});
