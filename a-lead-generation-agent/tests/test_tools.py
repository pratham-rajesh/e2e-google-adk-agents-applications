import csv

import httpx
import pytest

from lead_gen_agent.tools import web
from lead_gen_agent.tools.export import export_csv
from lead_gen_agent.tools.scoring import (
    lead_key,
    merge_leads,
    normalize_domain,
    normalize_name,
    rank_leads,
    score_lead,
)

BRIEF = {
    "target_industries": ["fintech", "payments"],
    "regions": ["India", "Singapore"],
    "buy_signals": ["hiring", "series b", "compliance"],
}


def test_normalize_domain_and_name():
    assert normalize_domain("https://www.Acme.io/about") == "acme.io"
    assert normalize_domain("acme.io") == "acme.io"
    assert normalize_domain("") == ""
    assert normalize_name("Acme Co. Ltd") == "acme"
    assert normalize_name("Foo-Bar, Inc.") == "foobar"


def test_lead_key_prefers_domain():
    assert lead_key({"company_name": "X", "website": "https://www.x.com"}) == "x.com"
    assert lead_key({"company_name": "X Inc"}) == "x"


def test_merge_dedupes_by_domain_and_fills_gaps():
    pool = [{"company_name": "Acme", "website": "https://acme.io", "source_urls": ["a"]}]
    new = [
        {"company_name": "ACME Inc", "website": "https://www.acme.io/", "hq_location": "Pune",
         "source_urls": ["b"]},
        {"company_name": "Beta", "website": "https://beta.io"},
        {"company_name": ""},
    ]
    merged, added = merge_leads(pool, new)
    assert added == 1 and len(merged) == 2
    acme = merged[0]
    assert acme["hq_location"] == "Pune" and acme["source_urls"] == ["a", "b"]
    assert pool[0].get("hq_location") is None  # input not mutated


def test_merge_dedupes_within_one_batch():
    merged, added = merge_leads([], [{"company_name": "Z", "website": "z.com"},
                                     {"company_name": "Z Ltd", "website": "https://z.com"}])
    assert (len(merged), added) == (1, 1)


def test_score_full_and_empty():
    strong = {
        "industry": "Fintech", "hq_location": "Bengaluru, India", "description": "Payments hiring",
        "why_fit": "raised Series B, compliance push", "source_urls": ["u1", "u2"],
        "enrichment": {"emails": ["a@b.com"], "social": ["https://linkedin.com/company/x"]},
    }
    assert score_lead(strong, BRIEF)["score"] == 100
    weak = {"industry": "Mining", "hq_location": "Chile"}
    assert score_lead(weak, BRIEF)["score"] == 0


def test_unspecified_criteria_do_not_penalise():
    s = score_lead({"industry": "x"}, {"target_industries": [], "regions": [], "buy_signals": []})
    assert s["breakdown"]["industry"] == 30 and s["breakdown"]["region"] == 20


def test_rank_orders_by_score_then_name():
    leads = [
        {"company_name": "B", "industry": "fintech", "hq_location": "India"},
        {"company_name": "A", "industry": "fintech", "hq_location": "India"},
        {"company_name": "C", "industry": "mining"},
    ]
    ranked = rank_leads(leads, BRIEF)
    assert [l["company_name"] for l in ranked] == ["A", "B", "C"]


def test_export_csv_roundtrip(tmp_path):
    ranked = rank_leads(
        [{"company_name": "Acme", "website": "acme.io", "source_urls": ["u"],
          "enrichment": {"emails": ["hi@acme.io"]}, "signals": "a\nb"}], BRIEF)
    path = export_csv(ranked, tmp_path)
    rows = list(csv.DictReader(path.open(encoding="utf-8")))
    assert rows[0]["company_name"] == "Acme" and rows[0]["emails"] == "hi@acme.io"
    assert rows[0]["signals"] == "a b"


HTML = """<html><head><title> Acme  Payments </title>
<meta name="description" content="We move money."></head>
<body>Contact sales@Acme.io or logo@x.png. Built on Stripe and Snowflake.
<a href="https://www.linkedin.com/company/acme">in</a><a href="https://example.com">e</a>
</body></html>"""


def test_extract_page_facts():
    f = web.extract_page_facts(HTML)
    assert f["title"] == "Acme Payments" and f["description"] == "We move money."
    assert f["emails"] == ["sales@acme.io"]
    assert f["social"] == ["https://www.linkedin.com/company/acme"]
    assert f["tech"] == ["Snowflake", "Stripe"]


@pytest.mark.parametrize(
    "url", ["ftp://example.com", "http://127.0.0.1/admin", "http://localhost:8080",
            "http://169.254.169.254/latest/meta-data", "http://10.0.0.5", "file:///etc/passwd"],
)
def test_validate_public_url_blocks_internal_targets(url):
    with pytest.raises(web.UnsafeUrlError):
        web.validate_public_url(url)


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_fetch_company_page_success():
    async with _client(lambda r: httpx.Response(200, text=HTML)) as c:
        out = await web.fetch_company_page("https://acme.io", client=c, check_dns=False)
    assert out["ok"] and out["emails"] == ["sales@acme.io"]


async def test_fetch_company_page_http_error_is_reported_not_raised():
    async with _client(lambda r: httpx.Response(503)) as c:
        out = await web.fetch_company_page("https://acme.io", client=c, check_dns=False)
    assert not out["ok"] and "503" in out["error"]


async def test_fetch_company_page_blocks_private_url_without_network():
    out = await web.fetch_company_page("http://127.0.0.1:9/")
    assert not out["ok"] and "UnsafeUrlError" in out["error"]
