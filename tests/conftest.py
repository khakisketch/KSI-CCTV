"""Suite-wide isolation for host-provided notification settings."""

import pytest

SMTP_ENVIRONMENT_KEYS = (
    "KSI_CCTV_SMTP_HOST",
    "KSI_CCTV_SMTP_PORT",
    "KSI_CCTV_SMTP_USER",
    "KSI_CCTV_SMTP_PASSWORD",
    "KSI_CCTV_SMTP_RECIPIENTS",
    "KSI_CCTV_SMTP_USE_SSL",
    "KSI_CCTV_SMTP_TIMEOUT",
)


@pytest.fixture(autouse=True)
def isolate_smtp_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prevent host SMTP configuration from starting real outbox delivery in tests."""
    for key in SMTP_ENVIRONMENT_KEYS:
        monkeypatch.delenv(key, raising=False)
