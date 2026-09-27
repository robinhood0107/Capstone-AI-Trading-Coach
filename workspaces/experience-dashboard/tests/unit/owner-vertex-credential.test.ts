import assert from 'node:assert/strict';
import test from 'node:test';
import { encodeServiceAccountJson, toFullRequest, type StrongLlmSettingsView } from '../../src/features/strong-llm/viewModel';

const FAKE_SA = {
  type: 'service_account',
  project_id: 'mars-test-dummy',
  private_key_id: '0123456789abcdef',
  private_key: '-----BEGIN PRIVATE KEY-----\nAAAA\n-----END PRIVATE KEY-----\n',
  client_email: 'dummy@mars-test-dummy.iam.gserviceaccount.com',
  client_id: '0',
  token_uri: 'https://oauth2.googleapis.com/token',
};

const VIEW: StrongLlmSettingsView = {
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
  usedToday: null,
};

test('a pasted service account file becomes one canonical base64 line the server accepts', () => {
  const encoded = encodeServiceAccountJson(`\n${JSON.stringify(FAKE_SA, null, 2)}\n`);
  assert.equal(encoded.ok, true);
  if (!encoded.ok) return;
  assert.match(encoded.value, /^[A-Za-z0-9+/]+={0,2}$/);
  const decoded = JSON.parse(Buffer.from(encoded.value, 'base64').toString('utf8'));
  assert.deepEqual(decoded, FAKE_SA);
});

test('API keys and unrelated JSON are refused before saving without echoing the input', () => {
  const apiKey = encodeServiceAccountJson('AIzaSyFAKEFAKEFAKEFAKE');
  assert.equal(apiKey.ok, false);
  if (!apiKey.ok) assert.doesNotMatch(apiKey.error, /AIza/);
  const other = encodeServiceAccountJson(JSON.stringify({ ...FAKE_SA, type: 'authorized_user' }));
  assert.equal(other.ok, false);
  assert.equal(encodeServiceAccountJson('   ').ok, false);
});

test('the FULL request is Vertex only, never sends a fallback provider, and keeps the key untouched when empty', () => {
  const keep = toFullRequest(VIEW, null, false, true);
  assert.equal(keep.provider, 'vertex');
  assert.equal(keep.fallbackProvider, null);
  assert.equal(keep.aiJudgementEnabled, true);
  assert.equal('apiKey' in keep, false);
  assert.equal(toFullRequest(VIEW, 'abc', false, false).apiKey, 'abc');
  assert.equal(toFullRequest(VIEW, 'abc', true, false).apiKey, '');
});
