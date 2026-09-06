import styles from '../intro.module.css';

/**
 * 소개 전용 클래스 조회.
 *
 * 소개 스타일은 `intro.module.css` 안에 갇혀 있어 클래스 이름이 빌드 때 해시된다.
 * 데모의 이름(`act--1`, `t-card__h` 처럼 하이픈이 든 것)을 그대로 쓰려면 대괄호
 * 접근이 필요한데, 마크업 곳곳에서 그러면 읽기가 나빠져 이 도우미로 모았다.
 *
 * 없는 이름을 넘기면 조용히 빠진다. 개발 중에는 콘솔로 알린다 — 오타 하나가
 * 스타일 통째로 사라지는 형태로 나타나기 때문이다.
 */
export function cx(...names: string[]): string {
  const out: string[] = [];
  for (const name of names) {
    const resolved = (styles as Record<string, string | undefined>)[name];
    if (resolved) out.push(resolved);
    else if (process.env.NODE_ENV !== 'production') {
      console.warn(`[intro] intro.module.css 에 '${name}' 클래스가 없습니다.`);
    }
  }
  return out.join(' ');
}
