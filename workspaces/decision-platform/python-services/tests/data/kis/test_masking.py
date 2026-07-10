from app.data.kis.masking import mask_secret, mask_text


def test_mask_secret_never_preserves_secret_fragments() -> None:
    assert mask_secret("abcdefghijklmnopqrstuvwxyz") == "[redacted]"
    assert mask_secret("short") == "[redacted]"


def test_mask_text_removes_known_sensitive_values() -> None:
    masked = mask_text("token=abc123 account=12345678", ["abc123", "12345678"])

    assert "abc123" not in masked
    assert "12345678" not in masked
