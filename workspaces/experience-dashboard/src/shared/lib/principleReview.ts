import { ID_PATTERN } from '@/shared/api/endpoints';

const KEY = 'p1.principleReview';
export const PRINCIPLE_EDIT_ANCHOR = 'principle-rules';

export function rememberReviewedPrinciple(principleId: string, version: number): void {
  try {
    window.sessionStorage.setItem(KEY, JSON.stringify({ principleId, version }));
  } catch {
    // The link can still open the principles screen.
  }
}

export function reviewedPrinciple(): { principleId: string; version: number } | null {
  try {
    if (window.location.hash !== '#' + PRINCIPLE_EDIT_ANCHOR) return null;
    const raw = window.sessionStorage.getItem(KEY);
    if (!raw) return null;
    const context = JSON.parse(raw);
    return typeof context?.principleId === 'string' && ID_PATTERN.principleId.test(context.principleId)
      && Number.isSafeInteger(context.version) && context.version > 0 ? context : null;
  } catch {
    return null;
  }
}
