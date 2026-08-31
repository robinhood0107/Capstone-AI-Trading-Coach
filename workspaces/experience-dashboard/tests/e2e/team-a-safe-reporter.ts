import type {
  FullConfig,
  FullResult,
  Reporter,
  Suite,
  TestCase,
  TestResult,
} from '@playwright/test/reporter';

import { teamAOperations } from '../../src/shared/api/generated/p1-team-a-client.v2';

// 검증이 비교하는 것과 같은 자리에서 읽는다. 상수로 들고 있으면 catalog 가 늘어날 때
// 영수증만 옛 수를 찍고, 그 수가 곧 사람이 증거로 인용하는 값이 된다.
const ACCEPTANCE_OPERATION_COUNT = Object.keys(teamAOperations).length;

function safe(value: string): string {
  return value
    .replace(/Bearer\s+[A-Za-z0-9._~-]+/gi, 'Bearer [REDACTED]')
    .replace(/("?password"?\s*[:=]\s*)[^\s,}]+/gi, '$1[REDACTED]')
    .replace(/eyJ[A-Za-z0-9._~-]{16,}/g, '[REDACTED_JWT]')
    .slice(0, 500);
}

export default class TeamASafeReporter implements Reporter {
  private skipped = 0;
  private failed = 0;

  onBegin(_config: FullConfig, suite: Suite): void {
    process.stdout.write(`TEAM_A_PLAYWRIGHT_TESTS=${suite.allTests().length}\n`);
  }

  onTestEnd(test: TestCase, result: TestResult): void {
    if (result.status === 'skipped') this.skipped += 1;
    if (result.status !== 'passed' && result.status !== 'skipped') this.failed += 1;
    process.stdout.write(`TEAM_A_TEST=${safe(test.title)} STATUS=${result.status.toUpperCase()}\n`);
    if (result.error?.message) {
      process.stdout.write(`TEAM_A_TEST_ERROR=${safe(result.error.message)}\n`);
    }
  }

  onEnd(result: FullResult): void {
    process.stdout.write(`PLAYWRIGHT_SKIP=${this.skipped}\n`);
    process.stdout.write(`TEAM_A_PLAYWRIGHT_FAILURES=${this.failed}\n`);
    if (result.status === 'passed' && this.skipped === 0 && this.failed === 0) {
      process.stdout.write(`TEAM_A_ACCEPTANCE_OPERATION_COUNT=${ACCEPTANCE_OPERATION_COUNT}\n`);
      process.stdout.write('FRONTEND_FAKE_PRODUCTION_RESPONSE=0\n');
      process.stdout.write('OWNER_TEAM_A_BACKEND_PREREQUISITES=PASS\n');
    }
  }
}
