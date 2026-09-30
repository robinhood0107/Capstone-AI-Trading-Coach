const MAX_SESSION_DAILY_LIMIT = 100;
const MAX_GLOBAL_DAILY_LIMIT = 1_000;

function integer(name: string, fallback: number, min: number, max: number): number {
  const raw = process.env[name];
  if (raw === undefined || raw.trim() === '') return fallback;
  if (!/^[0-9]+$/.test(raw)) throw new Error(`invalid DEMO setting: ${name}`);
  const value = Number(raw);
  if (!Number.isSafeInteger(value) || value < min || value > max) {
    throw new Error(`invalid DEMO setting: ${name}`);
  }
  return value;
}

function positiveRate(name: string, fallback: number): number {
  const raw = process.env[name];
  if (raw === undefined || raw.trim() === '') return fallback;
  const value = Number(raw);
  if (!Number.isFinite(value) || value <= 0 || value > 10_000) {
    throw new Error(`invalid DEMO setting: ${name}`);
  }
  return value;
}

export interface DemoAgentConfig {
  perSessionDailyLimit: number;
  globalDailyLimit: number;
  maxConcurrent: number;
  maxInputChars: number;
  maxInputBytes: number;
  maxContextChars: number;
  maxOutputTokens: number;
  perSessionRequestsPerMinute: number;
  globalRequestsPerMinute: number;
  timeoutMs: number;
  model: string;
  location: string;
  projectId: string;
  inputUsdPerMillionTokens: number;
  outputUsdPerMillionTokens: number;
  pricingReviewedAt: string;
  pricingSource: string;
}

export function readAgentConfig(): DemoAgentConfig {
  const model = process.env.MARS_DEMO_AGENT_MODEL ?? 'gemini-2.5-flash';
  const projectId = process.env.MARS_DEMO_VERTEX_PROJECT_ID ?? '';
  const location = process.env.MARS_DEMO_VERTEX_LOCATION ?? 'us-central1';
  const pricingReviewedAt = process.env.MARS_DEMO_AGENT_PRICING_REVIEWED_AT ?? '2026-09-30';
  if (!/^[a-z0-9][a-z0-9.-]{4,62}$/.test(model)) throw new Error('invalid DEMO setting: MARS_DEMO_AGENT_MODEL');
  if (!/^[a-z][a-z0-9-]{3,62}$/.test(location)) throw new Error('invalid DEMO setting: MARS_DEMO_VERTEX_LOCATION');
  if (projectId && !/^[a-z][a-z0-9-]{4,62}$/.test(projectId)) {
    throw new Error('invalid DEMO setting: MARS_DEMO_VERTEX_PROJECT_ID');
  }
  if (!/^20[0-9]{2}-[0-9]{2}-[0-9]{2}$/.test(pricingReviewedAt)) {
    throw new Error('invalid DEMO setting: MARS_DEMO_AGENT_PRICING_REVIEWED_AT');
  }
  const config = {
    perSessionDailyLimit: integer('MARS_DEMO_AGENT_PER_SESSION_DAILY_LIMIT', 5, 1, MAX_SESSION_DAILY_LIMIT),
    globalDailyLimit: integer('MARS_DEMO_AGENT_GLOBAL_DAILY_LIMIT', 50, 1, MAX_GLOBAL_DAILY_LIMIT),
    maxConcurrent: integer('MARS_DEMO_AGENT_MAX_CONCURRENT', 1, 1, 4),
    maxInputChars: integer('MARS_DEMO_AGENT_MAX_INPUT_CHARS', 1_500, 100, 10_000),
    maxInputBytes: integer('MARS_DEMO_AGENT_MAX_INPUT_TOKENS', 24_576, 1_024, 100_000),
    maxContextChars: integer('MARS_DEMO_AGENT_MAX_CONTEXT_CHARS', 4_000, 256, 20_000),
    maxOutputTokens: integer('MARS_DEMO_AGENT_MAX_OUTPUT_TOKENS', 512, 16, 8_192),
    perSessionRequestsPerMinute: integer('MARS_DEMO_AGENT_SESSION_REQUESTS_PER_MINUTE', 3, 1, 60),
    globalRequestsPerMinute: integer('MARS_DEMO_AGENT_GLOBAL_REQUESTS_PER_MINUTE', 15, 1, 600),
    timeoutMs: integer('MARS_DEMO_AGENT_TIMEOUT_MS', 45_000, 1_000, 90_000),
    model,
    location,
    projectId,
    inputUsdPerMillionTokens: positiveRate('MARS_DEMO_AGENT_INPUT_USD_PER_MILLION_TOKENS', 0.30),
    outputUsdPerMillionTokens: positiveRate('MARS_DEMO_AGENT_OUTPUT_USD_PER_MILLION_TOKENS', 2.50),
    pricingReviewedAt,
    pricingSource: 'https://cloud.google.com/gemini-enterprise-agent-platform/generative-ai/pricing (Gemini 2.5 Flash standard text rates, reviewed 2026-09-30)',
  } satisfies DemoAgentConfig;
  return config;
}

export function maxDailyCostUsd(config: DemoAgentConfig): number {
  return (
    config.globalDailyLimit *
    (config.maxInputBytes * config.inputUsdPerMillionTokens +
      config.maxOutputTokens * config.outputUsdPerMillionTokens) /
      1_000_000
  );
}
