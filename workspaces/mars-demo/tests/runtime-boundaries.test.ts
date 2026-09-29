import assert from 'node:assert/strict';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { marketPhaseAt } from '../src/server/clock';
import { projectBacktest, projectShowcase, scenarioAssumptions, showcaseMetadata, visibleBars, visibleShowcaseEvents } from '../src/server/ledger';
import { defaultOverlay, closeDatabase, databaseAt, finishAgentCall, getOverlay, kstDate, reserveAgentCall, saveOverlay, usageSnapshot } from '../src/server/store';
import { isSameOrigin, issueDemoSession, sessionHash, verifyDemoSession } from '../src/server/session';
import { NextRequest } from 'next/server';
import { readAgentConfig } from '../src/server/config';
import type { DemoAgentConfig } from '../src/server/config';

test('fake UTC clock maps to XKRX KST phases and closed days', () => {
  const beforeOpen = marketPhaseAt(new Date('2026-09-28T23:00:00.000Z'));
  assert.equal(beforeOpen.dateKst, '2026-09-29');
  assert.equal(beforeOpen.timeKst, '08:00:00');
  assert.equal(beforeOpen.phase, 'PREOPEN');

  const open = marketPhaseAt(new Date('2026-09-29T00:00:00.000Z'));
  assert.equal(open.timeKst, '09:00:00');
  assert.equal(open.phase, 'INTRADAY');

  assert.equal(marketPhaseAt(new Date('2026-09-29T06:30:00.000Z')).phase, 'CLOSE');
  assert.equal(marketPhaseAt(new Date('2026-09-29T07:30:00.000Z')).phase, 'AFTER_HOURS');
  assert.equal(marketPhaseAt(new Date('2026-09-29T12:00:00.000Z')).phase, 'NIGHT');

  const weekend = marketPhaseAt(new Date('2026-09-26T01:00:00.000Z'));
  assert.equal(weekend.phase, 'WEEKEND');
  assert.equal(weekend.isTradingSession, false);
  const holiday = marketPhaseAt(new Date('2026-09-25T01:00:00.000Z'));
  assert.equal(holiday.phase, 'HOLIDAY');
  assert.equal(holiday.isTradingSession, false);
  assert.equal(marketPhaseAt(new Date('2027-10-01T01:00:00.000Z')).phase, 'CALENDAR_UNAVAILABLE');
});

test('KST midnight, not UTC midnight, determines the daily quota bucket', () => {
  assert.equal(kstDate(new Date('2026-09-29T14:59:00.000Z')), '2026-09-29');
  assert.equal(kstDate(new Date('2026-09-29T15:01:00.000Z')), '2026-09-30');
});

test('same-origin writes compare the public Host/proxy scheme instead of Next internal origin', () => {
  const direct = new NextRequest('http://127.0.0.1:3000/api/demo/session', {
    headers: { host: '127.0.0.1:3000', origin: 'http://127.0.0.1:3000' },
  });
  assert.equal(isSameOrigin(direct), true);

  const behindTlsProxy = new NextRequest('http://127.0.0.1:3000/api/demo/session', {
    headers: {
      host: 'demo.example.test',
      origin: 'https://demo.example.test',
      'x-forwarded-proto': 'https',
    },
  });
  assert.equal(isSameOrigin(behindTlsProxy), true);

  const mismatched = new NextRequest('http://127.0.0.1:3000/api/demo/session', {
    headers: { host: 'demo.example.test', origin: 'https://evil.example.test', 'x-forwarded-proto': 'https' },
  });
  assert.equal(isSameOrigin(mismatched), false);
  const noOrigin = new NextRequest('http://127.0.0.1:3000/api/demo/session', {
    headers: { 'sec-fetch-site': 'same-origin' },
  });
  assert.equal(isSameOrigin(noOrigin), true);
});

test('session tokens are random per visitor, signed, expiring, and tamper evident', () => {
  const directory = mkdtempSync(path.join(os.tmpdir(), 'mars-demo-session-'));
  const keyPath = path.join(directory, 'session.key');
  writeFileSync(keyPath, Buffer.alloc(32, 7));
  const previous = process.env.MARS_DEMO_SESSION_SIGNING_KEY_FILE;
  process.env.MARS_DEMO_SESSION_SIGNING_KEY_FILE = keyPath;
  try {
    const now = Date.parse('2026-09-29T00:00:00.000Z');
    const first = issueDemoSession(now);
    const second = issueDemoSession(now);
    assert.notEqual(first.session.id, second.session.id);
    assert.deepEqual(verifyDemoSession(first.token, now), first.session);
    assert.equal(verifyDemoSession(first.token, now + 9 * 60 * 60 * 1_000), null);
    assert.equal(verifyDemoSession(`${first.token}tampered`, now), null);
    assert.notEqual(sessionHash(first.session.id), sessionHash(second.session.id));
  } finally {
    if (previous === undefined) delete process.env.MARS_DEMO_SESSION_SIGNING_KEY_FILE;
    else process.env.MARS_DEMO_SESSION_SIGNING_KEY_FILE = previous;
    rmSync(directory, { recursive: true, force: true });
  }
});

test('fixture projections hide future events and future daily bars', () => {
  const beforeEnd = new Date('2026-09-01T00:00:00.000Z');
  const events = visibleShowcaseEvents(beforeEnd, false);
  assert.equal(events.length, 0);
  assert.ok(visibleBars('2026-09-01').every((bar) => bar.date <= '2026-09-01'));
  const partialBacktest = projectBacktest('2026-09-01');
  assert.ok(partialBacktest.daily.every((point) => point.date <= '2026-09-01'));
  assert.ok(partialBacktest.events.every((event) => String(event.date) <= '2026-09-01'));

  const afterEnd = new Date('2026-09-29T00:00:00.000Z');
  const completedEvents = visibleShowcaseEvents(afterEnd, true);
  assert.ok(completedEvents.every((event) => Date.parse(event.atKst) <= afterEnd.getTime()));
  assert.equal(projectBacktest('2026-09-29').observedDays, 24);
});

test('the single scenario event log reconciles to the generated portfolio receipt', () => {
  assert.equal(scenarioAssumptions().kospiSellTaxBps, 20);
  const metadata = showcaseMetadata();
  const asOf = new Date('2026-10-01T00:00:00.000Z');
  const bars = visibleBars('2026-10-01');
  const projection = projectShowcase(metadata.events, defaultOverlay(), asOf, bars);
  const showcase = showcaseMetadata();
  const receipt = projectBacktest('2026-09-18');
  assert.equal(projection.equity, showcase.final.equity);
  assert.equal(projection.cash, showcase.final.cash);
  assert.equal(projection.latestVisibleDate, '2026-09-18');
  assert.equal(receipt.observedDays, 24);
  assert.equal(receipt.usesFutureData, false);
});

test('visitor overlays are isolated while agent quotas are global and survive restart', () => {
  const directory = mkdtempSync(path.join(os.tmpdir(), 'mars-demo-state-'));
  const filePath = path.join(directory, 'state.sqlite');
  const previous = process.env.MARS_DEMO_STATE_DB_PATH;
  process.env.MARS_DEMO_STATE_DB_PATH = filePath;
  const now = new Date('2026-09-29T00:00:00.000Z');
  const sessionA = 'a'.repeat(64);
  const sessionB = 'b'.repeat(64);
  const defaults = readAgentConfig();
  const config: DemoAgentConfig = {
    ...defaults,
    perSessionDailyLimit: 3,
    globalDailyLimit: 2,
    maxConcurrent: 4,
    perSessionRequestsPerMinute: 100,
    globalRequestsPerMinute: 100,
  };

  try {
    databaseAt(filePath);
    saveOverlay(sessionA, { ...defaultOverlay(), autoArmed: true }, now);
    assert.equal(getOverlay(sessionA).autoArmed, true);
    assert.equal(getOverlay(sessionB).autoArmed, false);

    const first = reserveAgentCall(sessionA, config, 1_000, now);
    assert.equal(first.ok, true);
    if (!first.ok) return;
    finishAgentCall(first.reservation.id, {
      status: 'SUCCEEDED', inputTokens: 80, outputTokens: 45,
      estimatedCostUsd: (80 * config.inputUsdPerMillionTokens + 45 * config.outputUsdPerMillionTokens) / 1_000_000,
    }, now);

    const second = reserveAgentCall(sessionA, config, 1_100, now);
    assert.equal(second.ok, true);
    if (!second.ok) return;
    const concurrentConfig = { ...config, maxConcurrent: 1, globalDailyLimit: 50 };
    const concurrent = reserveAgentCall(sessionB, concurrentConfig, 1_000, now);
    assert.equal(concurrent.ok, false);
    if (!concurrent.ok) assert.equal(concurrent.reason, 'CONCURRENT_LIMIT');

    const third = reserveAgentCall(sessionB, config, 1_200, now);
    assert.equal(third.ok, false);
    if (third.ok) return;
    assert.equal(third.reason, 'GLOBAL_LIMIT');
    assert.equal(usageSnapshot(sessionA, config, now).sessionCalls, 2);
    assert.equal(usageSnapshot(sessionB, config, now).sessionCalls, 0);
    assert.equal(usageSnapshot(sessionB, config, now).globalCalls, 2);

    const lowered = { ...config, globalDailyLimit: 1 };
    const fourth = reserveAgentCall(sessionB, lowered, 1_000, now);
    assert.equal(fourth.ok, false);
    if (!fourth.ok) assert.equal(fourth.reason, 'GLOBAL_LIMIT');

    const loweredSession = { ...config, perSessionDailyLimit: 1, globalDailyLimit: 50 };
    const fifth = reserveAgentCall(sessionA, loweredSession, 1_000, now);
    assert.equal(fifth.ok, false);
    if (!fifth.ok) assert.equal(fifth.reason, 'SESSION_LIMIT');

    closeDatabase(filePath);
    const reopened = databaseAt(filePath);
    const rows = reopened.prepare('SELECT status FROM agent_requests ORDER BY created_at, id').all() as { status: string }[];
    assert.ok(rows.some((row) => row.status === 'OUTCOME_UNKNOWN'));
    assert.equal(usageSnapshot(sessionA, config, now).globalCalls, 2);
    assert.equal(usageSnapshot(sessionA, config, now).activeCalls, 0);
    assert.equal(usageSnapshot(sessionA, config, now).unknownOutcomes, 1);
    const columns = reopened.prepare('PRAGMA table_info(agent_requests)').all() as { name: string }[];
    assert.ok(!columns.some((column) => /question|prompt|answer/i.test(column.name)));
  } finally {
    closeDatabase(filePath);
    if (previous === undefined) delete process.env.MARS_DEMO_STATE_DB_PATH;
    else process.env.MARS_DEMO_STATE_DB_PATH = previous;
    rmSync(directory, { recursive: true, force: true });
  }
});
