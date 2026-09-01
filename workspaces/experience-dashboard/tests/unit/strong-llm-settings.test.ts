import assert from 'node:assert/strict';
import test from 'node:test';
import {
  validationError,
  type StrongLlmSettingsView,
} from '../../src/features/strong-llm/viewModel.ts';

const base: StrongLlmSettingsView = {
  provider: 'vertex',
  fallbackProvider: '',
  modelId: '',
  fallbackModelId: '',
  baseUrl: '',
  fallbackBaseUrl: '',
  answerLanguage: 'ko',
  dailyGenerateCallCap: 50,
  keyLast4: null,
  fallbackKeyLast4: null,
  usedToday: 0,
  remaining: 50,
  effectiveDailyCap: 50,
};

test('fallback providers that require credentials cannot be saved without a key', () => {
  const missing = validationError({ ...base, fallbackProvider: 'openai' }, '', '');
  const supplied = validationError(
    { ...base, fallbackProvider: 'openai' },
    '',
    'test-key-1234',
  );
  const stored = validationError(
    { ...base, fallbackProvider: 'openai', fallbackKeyLast4: '1234' },
    '',
    '',
  );

  assert.match(missing ?? '', /2차 OpenAI.*API 키/);
  assert.equal(supplied, null);
  assert.equal(stored, null);
});
