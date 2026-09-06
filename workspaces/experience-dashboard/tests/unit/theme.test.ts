import assert from 'node:assert/strict';
import test from 'node:test';
import { runInNewContext } from 'node:vm';
import { THEME_BOOT_SCRIPT } from '../../src/shared/lib/theme.ts';

function boot(values: Record<string, string>) {
  const attributes: Record<string, string> = {};
  runInNewContext(THEME_BOOT_SCRIPT, {
    localStorage: { getItem: (k: string) => values[k] ?? null, setItem: (k: string, v: string) => { values[k] = v; } },
    document: { documentElement: { setAttribute: (k: string, v: string) => { attributes[k] = v; }, removeAttribute: (k: string) => { delete attributes[k]; } } },
  });
  return attributes['data-theme'];
}

test('소개 선택을 한 번 이관한 뒤 새 공통 선택을 유지한다', () => {
  const values = { 'capstone.intro.set.v1': 'dark', 'capstone.theme.v1': 'light' } as Record<string, string>;
  assert.equal(boot(values), 'dark');
  values['capstone.theme.v2'] = 'light';
  assert.equal(boot(values), 'light');
});

test('유효하지 않은 소개 값은 기존 대시보드 선택으로 이관한다', () => {
  assert.equal(boot({ 'capstone.intro.set.v1': 'invalid', 'capstone.theme.v1': 'dark' }), 'dark');
});

test('명시적 시스템 선택을 과거 어두운 선택으로 덮어쓰지 않는다', () => {
  assert.equal(boot({ 'capstone.theme.v2': 'system', 'capstone.intro.set.v1': 'dark' }), undefined);
});
