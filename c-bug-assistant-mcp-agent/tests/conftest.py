import pytest


@pytest.fixture(autouse=True)
def sandbox(monkeypatch, tmp_path):
    """Every test gets its own SQLite file (the MCP server subprocess inherits it) and a clean env."""
    db = tmp_path / "tickets.db"
    monkeypatch.setenv("BUGASSIST_DB", str(db))
    for var in ("BUGASSIST_PROVIDER", "BUGASSIST_MODEL", "BUGASSIST_RESEARCH_MODEL", "BUGASSIST_MAX_WRITES",
                "BUGASSIST_DUP_BLOCK", "BUGASSIST_REQUIRE_CONFIRMATION", "BUGASSIST_SEARCH_BACKEND"):
        monkeypatch.delenv(var, raising=False)
    return db
