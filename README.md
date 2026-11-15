# End-to-end Google ADK Agent Applications

Assignment 3: five production-style agents built with the [Google Agent Development Kit (ADK)](https://google.github.io/adk-docs/).
Each part is self-contained (own dependencies, tests, README with architecture diagram and
execution walkthrough, and a video script). See [PLAN.md](PLAN.md) for the roadmap and conventions.

| Part | Agent | Folder | Status | Video |
|---|---|---|---|---|
| A | Deep-research lead-generation agent | [a-lead-generation-agent](a-lead-generation-agent) | built, 37 offline tests | _to be added_ |
| B | Gemini CLI as a tool in an ADK pipeline | [b-gemini-cli-tool-agent](b-gemini-cli-tool-agent) | built, 61 offline tests | _to be added_ |
| C | MCP-tools bug assistant | [c-bug-assistant-mcp-agent](c-bug-assistant-mcp-agent) | built, 62 offline tests | _to be added_ |
| D | Production code-review assistant | d-code-review-assistant | planned | - |
| E | Production e-commerce agent | e-ecommerce-agent | planned | - |

Handover notes for continuing the work live in [docs/handover](docs/handover); video scripts in
[docs/video](docs/video).

## Quick start (any part)

```bash
cd <part-folder>
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pytest                                                 # offline, no API key needed
cp .env.example .env                                   # add GOOGLE_API_KEY for live runs
adk web
```
