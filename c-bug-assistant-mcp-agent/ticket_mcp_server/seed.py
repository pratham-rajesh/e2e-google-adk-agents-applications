"""Demo tickets for a fictional web shop ("Acme Orders"), so the agent has something to dedupe against."""

SEED_TICKETS = [
    {
        "title": "Checkout fails with HTTP 500 when the cart contains a discontinued product",
        "description": "POST /api/checkout returns 500. Stack trace shows NullPointerException in "
                       "PriceCalculator.lineTotal because Product.price is null for discontinued items.",
        "priority": "high", "component": "checkout", "reporter": "support",
        "comments": [{"author": "dana", "body": "Reproduced on staging. Discontinued SKUs have price=NULL after the catalogue import."}],
    },
    {
        "title": "Database connection pool exhausted under load: timeout waiting for connection",
        "description": "During the 09:00 traffic peak requests fail with 'HikariPool-1 - Connection is not available, "
                       "request timed out after 30000ms'. Pool size is 10; long-running report queries hold connections.",
        "priority": "critical", "component": "backend", "reporter": "oncall", "status": "in_progress",
        "comments": [{"author": "lee", "body": "Raised pool size to 30 as a stopgap. Root cause: report export does not release the connection on error."}],
    },
    {
        "title": "Invoice date shown one day earlier for customers in UTC-8",
        "description": "Invoice PDFs print the creation date in UTC and then format it in the browser timezone, "
                       "so evening orders in California show yesterday's date.",
        "priority": "medium", "component": "billing", "reporter": "support", "status": "resolved",
        "comments": [{"author": "sam", "body": "Fixed by storing order timestamps as timezone-aware and formatting server-side in the customer's zone."}],
    },
    {
        "title": "Product search endpoint is slow (8s) for queries with more than three words",
        "description": "GET /api/search?q=... does a LIKE '%word%' scan on products.description. No index. "
                       "p95 latency 8 seconds on production.",
        "priority": "medium", "component": "search", "reporter": "perf-team",
    },
    {
        "title": "Login redirect loop after session cookie expires on Safari",
        "description": "Safari users are bounced between /login and /account forever once the session cookie "
                       "expires. SameSite=None without Secure is rejected by Safari, so the new cookie is dropped.",
        "priority": "high", "component": "auth", "reporter": "qa",
        "comments": [{"author": "qa", "body": "Only on Safari 17 and iOS. Chrome and Firefox are fine."}],
    },
    {
        "title": "Memory leak in the order-export worker, heap grows until OOMKilled",
        "description": "The Kubernetes pod running export-worker is OOMKilled every ~6h. Heap dump shows millions of "
                       "retained OrderRow objects held by a static cache that is never cleared.",
        "priority": "high", "component": "workers", "reporter": "sre",
    },
    {
        "title": "CSV export garbles non-ASCII customer names (UnicodeEncodeError)",
        "description": "Exporting customers with names such as 'Zoe' with accents raises UnicodeEncodeError: "
                       "'charmap' codec can't encode character. Default encoding on the export host is cp1252.",
        "priority": "low", "component": "export", "reporter": "support", "status": "closed",
        "comments": [{"author": "ana", "body": "Always open the output file with encoding='utf-8-sig' so Excel detects it."}],
    },
    {
        "title": "Flaky integration test: test_order_confirmation_email fails roughly 1 run in 10",
        "description": "The test reads the outbox before the async mail task has run. Passes locally, fails on CI under load.",
        "priority": "low", "component": "tests", "reporter": "ci-bot",
    },
]
