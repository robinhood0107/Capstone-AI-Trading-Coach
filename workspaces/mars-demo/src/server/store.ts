import Database from 'better-sqlite3';
import { mkdirSync } from 'node:fs';
import path from 'node:path';
import { randomUUID } from 'node:crypto';
import type { DemoAgentConfig } from './config';
import { maxDailyCostUsd } from './config';
import type { DemoOverlay } from '../shared/contracts';

export interface Reservation {
  id: string;
  usageDate: string;
  maxCostUsd: number;
}

export interface ReserveFailure {
  ok: false;
  reason: 'SESSION_LIMIT' | 'GLOBAL_LIMIT' | 'CONCURRENT_LIMIT' | 'RATE_LIMIT';
  used: number;
  limit: number;
}

export type ReserveResult = { ok: true; reservation: Reservation } | ReserveFailure;

const instances = new Map<string, Database.Database>();
const processId = randomUUID();

export function defaultOverlay(): DemoOverlay {
  return {
    autoArmed: false,
    riskProfile: 'balanced',
    dailyLossLimitPct: 2,
    virtualEvents: [],
    notes: [],
    globalKillSwitchActive: false,
    ragConsent: false,
    decisions: [],
    journalEntries: [],
    principleHistory: [],
  };
}

export function databasePath(): string {
  const configured = process.env.MARS_DEMO_STATE_DB_PATH;
  if (configured) return configured;
  if (process.env.NODE_ENV === 'development') return path.join('/tmp', `mars-demo-dev-${process.pid}.sqlite`);
  return '/data/mars-demo-state.sqlite';
}

export function databaseAt(filePath: string): Database.Database {
  const cached = instances.get(filePath);
  if (cached) return cached;
  mkdirSync(path.dirname(filePath), { recursive: true, mode: 0o700 });
  const db = new Database(filePath, { timeout: 5_000 });
  db.pragma('journal_mode = WAL');
  db.pragma('synchronous = FULL');
  db.pragma('foreign_keys = ON');
  db.pragma('busy_timeout = 5000');
  db.exec(`
    CREATE TABLE IF NOT EXISTS schema_version (
      version INTEGER PRIMARY KEY CHECK(version = 1)
    );
    INSERT OR IGNORE INTO schema_version(version) VALUES (1);
    CREATE TABLE IF NOT EXISTS session_overlay (
      session_hash TEXT PRIMARY KEY,
      payload_json TEXT NOT NULL,
      updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS global_daily_usage (
      usage_date TEXT PRIMARY KEY,
      calls INTEGER NOT NULL CHECK(calls >= 0)
    );
    CREATE TABLE IF NOT EXISTS session_daily_usage (
      usage_date TEXT NOT NULL,
      session_hash TEXT NOT NULL,
      calls INTEGER NOT NULL CHECK(calls >= 0),
      PRIMARY KEY(usage_date, session_hash)
    );
    CREATE TABLE IF NOT EXISTS agent_requests (
      id TEXT PRIMARY KEY,
      usage_date TEXT NOT NULL,
      session_hash TEXT NOT NULL,
      status TEXT NOT NULL,
      created_at TEXT NOT NULL,
      completed_at TEXT,
      input_bytes INTEGER NOT NULL,
      actual_input_tokens INTEGER,
      actual_output_tokens INTEGER,
      estimated_cost_usd REAL,
      max_cost_usd REAL NOT NULL,
      model TEXT NOT NULL,
      provider_latency_ms INTEGER
    );
    CREATE INDEX IF NOT EXISTS idx_agent_requests_session_created
      ON agent_requests(session_hash, created_at);
    CREATE INDEX IF NOT EXISTS idx_agent_requests_day
      ON agent_requests(usage_date, created_at);
    CREATE TABLE IF NOT EXISTS active_agent_calls (
      request_id TEXT PRIMARY KEY REFERENCES agent_requests(id) ON DELETE CASCADE,
      session_hash TEXT NOT NULL,
      process_id TEXT NOT NULL,
      created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS session_issuance_minutes (
      minute_key TEXT PRIMARY KEY,
      calls INTEGER NOT NULL CHECK(calls >= 0)
    );
  `);

  const recover = db.transaction(() => {
    db.prepare(`UPDATE agent_requests
      SET status = 'OUTCOME_UNKNOWN', completed_at = ?
      WHERE status = 'RESERVED'`).run(new Date().toISOString());
    db.prepare('DELETE FROM active_agent_calls').run();
    db.prepare("DELETE FROM session_overlay WHERE julianday(updated_at) < julianday('now', '-30 days')").run();
    db.prepare("DELETE FROM agent_requests WHERE julianday(created_at) < julianday('now', '-90 days')").run();
    db.prepare("DELETE FROM global_daily_usage WHERE usage_date < date('now', '-90 days')").run();
    db.prepare("DELETE FROM session_daily_usage WHERE usage_date < date('now', '-90 days')").run();
    db.prepare("DELETE FROM session_issuance_minutes WHERE minute_key < strftime('%Y-%m-%dT%H:%M', 'now', '-2 days')").run();
  });
  recover.immediate();
  instances.set(filePath, db);
  return db;
}

export function getDatabase(): Database.Database {
  return databaseAt(databasePath());
}

export function closeDatabase(filePath: string): void {
  const db = instances.get(filePath);
  if (!db) return;
  db.close();
  instances.delete(filePath);
}

export function getOverlay(sessionHash: string): DemoOverlay {
  const row = getDatabase()
    .prepare('SELECT payload_json FROM session_overlay WHERE session_hash = ?')
    .get(sessionHash) as { payload_json: string } | undefined;
  if (!row) return defaultOverlay();
  try {
    const parsed = JSON.parse(row.payload_json) as DemoOverlay;
    if (!Array.isArray(parsed.virtualEvents) || !Array.isArray(parsed.notes)) return defaultOverlay();
    return parsed;
  } catch {
    return defaultOverlay();
  }
}

export function saveOverlay(sessionHash: string, overlay: DemoOverlay, now = new Date()): void {
  getDatabase()
    .prepare(`INSERT INTO session_overlay(session_hash, payload_json, updated_at)
      VALUES (?, ?, ?)
      ON CONFLICT(session_hash) DO UPDATE SET payload_json = excluded.payload_json, updated_at = excluded.updated_at`)
    .run(sessionHash, JSON.stringify(overlay), now.toISOString());
}

export function resetOverlay(sessionHash: string): void {
  getDatabase().prepare('DELETE FROM session_overlay WHERE session_hash = ?').run(sessionHash);
}

export function reserveSessionIssue(now = new Date(), perMinuteLimit = 30): boolean {
  const db = getDatabase();
  const minuteKey = now.toISOString().slice(0, 16);
  const result = db.transaction(() => {
    const row = db.prepare('SELECT calls FROM session_issuance_minutes WHERE minute_key = ?').get(minuteKey) as { calls: number } | undefined;
    if ((row?.calls ?? 0) >= perMinuteLimit) return false;
    db.prepare(`INSERT INTO session_issuance_minutes(minute_key, calls) VALUES (?, 1)
      ON CONFLICT(minute_key) DO UPDATE SET calls = calls + 1`).run(minuteKey);
    return true;
  });
  return result.immediate();
}

export function reserveAgentCall(
  sessionHash: string,
  config: DemoAgentConfig,
  inputBytes: number,
  now = new Date(),
): ReserveResult {
  const db = getDatabase();
  const usageDate = kstDate(now);
  const createdAt = now.toISOString();
  const maxCostUsd =
    (config.maxInputBytes * config.inputUsdPerMillionTokens +
      config.maxOutputTokens * config.outputUsdPerMillionTokens) /
    1_000_000;
  const transaction = db.transaction((): ReserveResult => {
    const globalRow = db.prepare('SELECT calls FROM global_daily_usage WHERE usage_date = ?').get(usageDate) as { calls: number } | undefined;
    const sessionRow = db.prepare('SELECT calls FROM session_daily_usage WHERE usage_date = ? AND session_hash = ?').get(usageDate, sessionHash) as { calls: number } | undefined;
    const globalUsed = globalRow?.calls ?? 0;
    const sessionUsed = sessionRow?.calls ?? 0;
    if (sessionUsed >= config.perSessionDailyLimit) {
      return { ok: false, reason: 'SESSION_LIMIT', used: sessionUsed, limit: config.perSessionDailyLimit };
    }
    if (globalUsed >= config.globalDailyLimit) {
      return { ok: false, reason: 'GLOBAL_LIMIT', used: globalUsed, limit: config.globalDailyLimit };
    }
    const active = db.prepare('SELECT COUNT(*) AS count FROM active_agent_calls').get() as { count: number };
    if (active.count >= config.maxConcurrent) {
      return { ok: false, reason: 'CONCURRENT_LIMIT', used: active.count, limit: config.maxConcurrent };
    }
    const since = new Date(now.getTime() - 60_000).toISOString();
    const sessionRate = db.prepare('SELECT COUNT(*) AS count FROM agent_requests WHERE session_hash = ? AND created_at >= ?').get(sessionHash, since) as { count: number };
    if (sessionRate.count >= config.perSessionRequestsPerMinute) {
      return { ok: false, reason: 'RATE_LIMIT', used: sessionRate.count, limit: config.perSessionRequestsPerMinute };
    }
    const globalRate = db.prepare('SELECT COUNT(*) AS count FROM agent_requests WHERE created_at >= ?').get(since) as { count: number };
    if (globalRate.count >= config.globalRequestsPerMinute) {
      return { ok: false, reason: 'RATE_LIMIT', used: globalRate.count, limit: config.globalRequestsPerMinute };
    }
    const id = randomUUID();
    db.prepare(`INSERT INTO agent_requests
      (id, usage_date, session_hash, status, created_at, input_bytes, max_cost_usd, model)
      VALUES (?, ?, ?, 'RESERVED', ?, ?, ?, ?)`)
      .run(id, usageDate, sessionHash, createdAt, inputBytes, maxCostUsd, config.model);
    db.prepare(`INSERT INTO active_agent_calls(request_id, session_hash, process_id, created_at)
      VALUES (?, ?, ?, ?)`)
      .run(id, sessionHash, processId, createdAt);
    db.prepare(`INSERT INTO global_daily_usage(usage_date, calls) VALUES (?, 1)
      ON CONFLICT(usage_date) DO UPDATE SET calls = calls + 1`).run(usageDate);
    db.prepare(`INSERT INTO session_daily_usage(usage_date, session_hash, calls) VALUES (?, ?, 1)
      ON CONFLICT(usage_date, session_hash) DO UPDATE SET calls = calls + 1`).run(usageDate, sessionHash);
    return { ok: true, reservation: { id, usageDate, maxCostUsd } };
  });
  return transaction.immediate();
}

export function finishAgentCall(
  id: string,
  outcome: {
    status: 'SUCCEEDED' | 'FAILED_PROVIDER' | 'OUTCOME_UNKNOWN' | 'USAGE_UNKNOWN';
    inputTokens?: number;
    outputTokens?: number;
    estimatedCostUsd?: number;
    latencyMs?: number;
  },
  now = new Date(),
): void {
  const db = getDatabase();
  const transaction = db.transaction(() => {
    db.prepare(`UPDATE agent_requests SET status = ?, completed_at = ?, actual_input_tokens = ?,
      actual_output_tokens = ?, estimated_cost_usd = ?, provider_latency_ms = ? WHERE id = ?`)
      .run(
        outcome.status,
        now.toISOString(),
        outcome.inputTokens ?? null,
        outcome.outputTokens ?? null,
        outcome.estimatedCostUsd ?? null,
        outcome.latencyMs ?? null,
        id,
      );
    db.prepare('DELETE FROM active_agent_calls WHERE request_id = ?').run(id);
  });
  transaction.immediate();
}

export function usageSnapshot(sessionHash: string, config: DemoAgentConfig, now = new Date()) {
  const db = getDatabase();
  const usageDate = kstDate(now);
  const global = db.prepare('SELECT calls FROM global_daily_usage WHERE usage_date = ?').get(usageDate) as { calls: number } | undefined;
  const session = db.prepare('SELECT calls FROM session_daily_usage WHERE usage_date = ? AND session_hash = ?').get(usageDate, sessionHash) as { calls: number } | undefined;
  const sessionRows = db.prepare(`SELECT status, estimated_cost_usd, max_cost_usd, actual_input_tokens, actual_output_tokens FROM agent_requests
    WHERE usage_date = ? AND session_hash = ?`)
    .all(usageDate, sessionHash) as { status: string; estimated_cost_usd: number | null; max_cost_usd: number; actual_input_tokens: number | null; actual_output_tokens: number | null }[];
  const globalRows = db.prepare(`SELECT status, estimated_cost_usd, max_cost_usd, actual_input_tokens, actual_output_tokens FROM agent_requests WHERE usage_date = ?`)
    .all(usageDate) as { status: string; estimated_cost_usd: number | null; max_cost_usd: number; actual_input_tokens: number | null; actual_output_tokens: number | null }[];
  const estimate = (items: { status: string; estimated_cost_usd: number | null; max_cost_usd: number }[]) => items.reduce((total, item) => {
    if (item.estimated_cost_usd !== null) return total + item.estimated_cost_usd;
    if (item.status === 'OUTCOME_UNKNOWN' || item.status === 'USAGE_UNKNOWN' || item.status === 'FAILED_PROVIDER') {
      return total + item.max_cost_usd;
    }
    return total;
  }, 0);
  const unknown = sessionRows.filter((item) => item.status === 'OUTCOME_UNKNOWN' || item.status === 'USAGE_UNKNOWN').length;
  return {
    sessionCalls: session?.calls ?? 0,
    sessionLimit: config.perSessionDailyLimit,
    globalCalls: global?.calls ?? 0,
    globalLimit: config.globalDailyLimit,
    remainingSessionCalls: Math.max(0, config.perSessionDailyLimit - (session?.calls ?? 0)),
    remainingGlobalCalls: Math.max(0, config.globalDailyLimit - (global?.calls ?? 0)),
    estimatedSessionCostUsd: estimate(sessionRows),
    estimatedGlobalCostUsd: estimate(globalRows),
    sessionInputTokens: sessionRows.reduce((total, item) => total + (item.actual_input_tokens ?? 0), 0),
    sessionOutputTokens: sessionRows.reduce((total, item) => total + (item.actual_output_tokens ?? 0), 0),
    globalInputTokens: globalRows.reduce((total, item) => total + (item.actual_input_tokens ?? 0), 0),
    globalOutputTokens: globalRows.reduce((total, item) => total + (item.actual_output_tokens ?? 0), 0),
    unknownOutcomes: unknown,
    activeCalls: (db.prepare('SELECT COUNT(*) AS count FROM active_agent_calls').get() as { count: number }).count,
    maxDailyCostUsd: maxDailyCostUsd(config),
    model: config.model,
    pricingSource: config.pricingSource,
    pricingReviewedAt: config.pricingReviewedAt,
  };
}

export function kstDate(now: Date): string {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Seoul',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).formatToParts(now);
  const pick = (type: string) => parts.find((part) => part.type === type)?.value ?? '';
  return `${pick('year')}-${pick('month')}-${pick('day')}`;
}
