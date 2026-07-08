from __future__ import annotations


def mask_secret(value: str | None) -> str:
    if not value or len(value) < 12:
        return "***"
    return f"{value[:4]}...{value[-4:]}"


def mask_text(text: str, secrets: list[str | None]) -> str:
    masked = text
    for secret in secrets:
        if secret:
            masked = masked.replace(secret, mask_secret(secret))
    return masked
