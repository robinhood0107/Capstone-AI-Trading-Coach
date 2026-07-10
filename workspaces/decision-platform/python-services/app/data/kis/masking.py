from __future__ import annotations


def mask_secret(value: str | None) -> str:
    # 길이·앞뒤 조각도 credential 식별 단서이므로 값 유무와 무관하게 같은 문자열만 반환한다.
    return "[redacted]"


def mask_text(text: str, secrets: list[str | None]) -> str:
    masked = text
    for secret in secrets:
        if secret:
            # KIS 오류 본문에는 입력 app key/secret이 되돌아올 수 있어 알려진 값 기준으로 먼저 치환한다.
            masked = masked.replace(secret, mask_secret(secret))
    return masked
