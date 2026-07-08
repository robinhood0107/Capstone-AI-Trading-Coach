from __future__ import annotations


def mask_secret(value: str | None) -> str:
    if not value or len(value) < 12:
        # 짧은 값은 앞뒤 일부만 보여도 전체 추측이 쉬워서 전부 가린다.
        return "***"
    return f"{value[:4]}...{value[-4:]}"


def mask_text(text: str, secrets: list[str | None]) -> str:
    masked = text
    for secret in secrets:
        if secret:
            # KIS 오류 본문에는 입력 app key/secret이 되돌아올 수 있어 알려진 값 기준으로 먼저 치환한다.
            masked = masked.replace(secret, mask_secret(secret))
    return masked
