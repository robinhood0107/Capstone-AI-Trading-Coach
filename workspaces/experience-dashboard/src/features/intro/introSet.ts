'use client';

import { applyTheme, resolvedTheme } from '@/shared/lib/theme';

/** 소개의 표시 속성은 유지하되 선택 권위는 공통 테마 하나다. */
export type IntroSet = 'light' | 'dark';
export const resolveIntroSet = resolvedTheme;
export const rememberIntroSet = applyTheme;
