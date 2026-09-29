import evidenceData from '../../data/evidence.v1.json';
import type { DemoEvidence } from '../shared/contracts';

const sources = evidenceData.sources as DemoEvidence[];
const stopWords = new Set(['그리고', '에서', '하는', '합니다', '어떻게', '무엇', '인가요', '해주세요', 'about', 'what', 'does', 'the', 'and', 'for', 'with']);

function tokens(value: string): string[] {
  return (value.normalize('NFKC').toLocaleLowerCase('ko-KR').match(/[\p{L}\p{N}]+/gu) ?? [])
    .filter((token) => token.length > 1 && !stopWords.has(token));
}

export function retrieveEvidence(question: string, limit = 3): DemoEvidence[] {
  const terms = new Set(tokens(question));
  if (terms.size === 0) return [];
  return sources
    .map((source) => {
      const sourceTerms = new Set(tokens(`${source.title} ${source.text} ${source.tags.join(' ')}`));
      let score = 0;
      for (const term of terms) if (sourceTerms.has(term)) score += 1;
      return { source, score };
    })
    .filter((item) => item.score > 0)
    .sort((left, right) => right.score - left.score || left.source.id.localeCompare(right.source.id))
    .slice(0, limit)
    .map((item) => item.source);
}

export function evidenceIndexVersion(): string {
  return evidenceData.indexVersion;
}
