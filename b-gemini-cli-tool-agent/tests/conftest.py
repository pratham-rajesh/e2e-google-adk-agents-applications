import json
import sys
from pathlib import Path

import pytest

from gemini_cli_agent import config

FAKE = str(Path(__file__).with_name("fake_gemini.py"))


@pytest.fixture(autouse=True)
def sandbox(monkeypatch, tmp_path):
    """Every test gets its own workspace/output dir and the fake `gemini` executable."""
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "proj").mkdir()
    monkeypatch.setenv("GEMCLI_WORKSPACE", str(ws))
    monkeypatch.setenv("GEMCLI_OUTPUT_DIR", str(tmp_path / "out"))
    monkeypatch.setenv("GEMCLI_BIN", json.dumps([sys.executable, FAKE]))
    for var in ("GEMCLI_APPROVAL_MODE", "GEMCLI_ALLOW_YOLO", "GEMCLI_CLI_MODEL", "GEMCLI_SANDBOX",
                "GEMCLI_SKIP_TRUST", "GEMCLI_TIMEOUT", "GEMCLI_MAX_OUTPUT_CHARS", "GEMCLI_PROVIDER"):
        monkeypatch.delenv(var, raising=False)
    # the fake executable is steered through env; production code never forwards this variable
    monkeypatch.setattr(config, "ENV_PASSTHROUGH", (*config.ENV_PASSTHROUGH, "FAKE_GEMINI_MODE"))
    monkeypatch.setenv("SUPER_SECRET_TOKEN", "should-not-leak")
    return ws
