import pytest

ENV_VARS = ("CODEREVIEW_PROVIDER", "CODEREVIEW_MODEL", "CODEREVIEW_CODER_MODEL", "CODEREVIEW_SANDBOX",
            "CODEREVIEW_SANDBOX_TIMEOUT", "CODEREVIEW_ALLOW_RISKY_EXEC", "CODEREVIEW_FIX_MAX_ITERATIONS",
            "CODEREVIEW_MAX_CODE_CHARS", "CODEREVIEW_ARTIFACT_DIR", "CODEREVIEW_SERVER_TOKEN",
            "CODEREVIEW_SANDBOX_MAX_OUTPUT", "CODEREVIEW_STRICT_SCHEMA", "CODEREVIEW_MAX_LINE_LENGTH", "CODEREVIEW_COMPLEXITY_LIMIT")


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Every test starts with default settings, whatever the developer's shell exports."""
    for var in ENV_VARS:
        monkeypatch.delenv(var, raising=False)
