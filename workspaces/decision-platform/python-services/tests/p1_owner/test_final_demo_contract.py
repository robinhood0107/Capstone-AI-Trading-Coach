from pathlib import Path


REPOSITORY = Path(__file__).resolve().parents[5]


def test_operator_rotation_updates_all_user_credential_consumers() -> None:
    control = (REPOSITORY / "deploy/p1/full-appctl").read_text(encoding="utf-8")

    assert "credential rotate <user|admin> /absolute/password-file" in control
    assert "15 <= len(raw) <= 64" in control
    assert "DEMO_CREDENTIAL_ROTATION_ACTOR=operator-cli" in control
    assert "P1_AUTOMATION_OWNER_PASSWORD" in control
    assert "SESSIONS_INVALIDATED=YES" in control
