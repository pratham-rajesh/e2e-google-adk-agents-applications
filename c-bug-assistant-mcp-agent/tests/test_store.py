"""The SQLite store and similarity scoring: deterministic, no MCP, no LLM."""

import sqlite3

import pytest

from ticket_mcp_server import similarity as sim
from ticket_mcp_server.seed import SEED_TICKETS
from ticket_mcp_server.store import TicketError, TicketStore


@pytest.fixture
def store(tmp_path):
    s = TicketStore(tmp_path / "t.db")
    s.seed(SEED_TICKETS)
    return s


def test_seed_is_idempotent(store):
    assert store.count() == len(SEED_TICKETS)
    assert store.seed(SEED_TICKETS) == 0
    assert store.count() == len(SEED_TICKETS)


def test_seed_keeps_status_and_comments(store):
    resolved = [t for t in store.search("invoice date timezone") if t["status"] == "resolved"]
    assert resolved
    full = store.get(resolved[0]["id"])
    assert full["comments"] and "timezone" in full["comments"][0]["body"]


def test_search_ranks_by_relevance_and_explains(store):
    hits = store.search("connection pool timeout")
    assert hits[0]["component"] == "backend"
    assert {"connection", "pool", "timeout"} <= set(hits[0]["matched_terms"])
    assert hits[0]["score"] >= hits[-1]["score"]


def test_search_filters_and_empty_query_lists_newest(store):
    assert {t["component"] for t in store.search("", component="AUTH")} == {"auth"}
    assert all(t["status"] == "closed" for t in store.search("", status="closed"))
    newest = store.search("", limit=3)
    assert [t["id"] for t in newest] == sorted((t["id"] for t in newest), reverse=True)


def test_find_duplicates_finds_the_real_duplicate_not_noise(store):
    dups = store.find_duplicates(
        "500 error at checkout for discontinued products",
        "NullPointerException in PriceCalculator because the price is null")
    assert dups and dups[0]["component"] == "checkout" and dups[0]["score"] >= 0.35
    assert "nullpointerexception" in dups[0]["matched_terms"]


def test_find_duplicates_returns_nothing_for_unrelated_report(store):
    assert store.find_duplicates("Dark mode toggle icon misaligned on settings page") == []


def test_create_get_roundtrip_and_defaults(store):
    t = store.create("Settings page crashes on save", "TypeError: x is undefined", "high", "frontend")
    assert t["status"] == "open" and t["priority"] == "high" and t["comments"] == []
    assert store.get(t["id"])["title"] == "Settings page crashes on save"


def test_update_and_comment_bump_updated_at(store):
    before = store.get(4)
    updated = store.update(4, status="in_progress", priority="high")
    assert (updated["status"], updated["priority"]) == ("in_progress", "high")
    commented = store.add_comment(4, "Reproduced on prod, p95 is 9s now.")
    assert commented["comments"][-1]["author"] == "bug-assistant"
    assert commented["updated_at"] >= before["updated_at"]


@pytest.mark.parametrize("call", [
    lambda s: s.create("", "x"),
    lambda s: s.create("t" * 201, "x"),
    lambda s: s.create("ok", "x", priority="urgent"),
    lambda s: s.update(1, status="done"),
    lambda s: s.update(1),
    lambda s: s.update(9999, status="open"),
    lambda s: s.get("abc"),
    lambda s: s.get(0),
    lambda s: s.add_comment(9999, "hi"),
    lambda s: s.add_comment(1, "   "),
    lambda s: s.search("x", status="bogus"),
])
def test_invalid_input_is_refused(store, call):
    with pytest.raises(TicketError):
        call(store)


def test_sql_injection_is_inert(store):
    evil = "x'); DROP TABLE tickets; --"
    t = store.create(evil, "payload " + evil)
    assert store.get(t["id"])["title"] == evil
    assert store.search(evil) is not None
    store.add_comment(t["id"], evil)
    with sqlite3.connect(store.db_path) as db:  # tables still exist
        assert db.execute("SELECT COUNT(*) FROM tickets").fetchone()[0] == len(SEED_TICKETS) + 1
    with pytest.raises(TicketError):
        store.search("", component="x' OR '1'='1", status="open'; --")


def test_nul_bytes_stripped(store):
    assert "\x00" not in store.create("bad\x00title", "d")["title"]


def test_tokenizer_and_similarity_basics():
    assert "the" not in sim.tokenize("the pool is exhausted")
    assert sim.similarity("pool exhausted", "", "pool exhausted", "") == pytest.approx(1.0)
    assert sim.similarity("pool exhausted", "", "dark mode icon", "") == 0.0
    assert sim.tokenize("timeouts timed out") == sim.tokenize("timeout timed out")  # light stemming
