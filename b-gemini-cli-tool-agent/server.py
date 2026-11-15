"""HTTP service around the pipeline (FastAPI), the thing the Dockerfile runs.

  POST /analyze   {"request": "..."}  -> verdict, answer, plan, CLI call log     (this app)
  GET  /healthz                                                                  (this app)
  /run, /run_sse, /list-apps, ...   the standard ADK API server                  (ADK)

Set GEMCLI_SERVER_TOKEN to require `Authorization: Bearer <token>` on /analyze. Every request spawns a
paid-for LLM run plus a CLI subprocess, so a public deployment should never be anonymous.
"""

from __future__ import annotations

import asyncio
import hmac
import os
from pathlib import Path
from typing import Callable

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from gemini_cli_agent import config
from gemini_cli_agent.runner import run_once

ROOT = Path(__file__).resolve().parent


class AnalyzeRequest(BaseModel):
    request: str = Field(min_length=3, max_length=8000, description="What you want Gemini CLI to do")


def create_app(pipeline_factory: Callable | None = None, with_adk_api: bool = True) -> FastAPI:
    if with_adk_api:
        from google.adk.cli.fast_api import get_fast_api_app

        app = get_fast_api_app(agents_dir=str(ROOT), web=os.getenv("GEMCLI_WEB_UI") == "1")
    else:
        app = FastAPI()
    app.title = "Gemini CLI tool agent"

    if pipeline_factory is None:
        from gemini_cli_agent.pipeline import build_pipeline as pipeline_factory  # noqa: N813

    limit = asyncio.Semaphore(int(os.getenv("GEMCLI_MAX_CONCURRENT_RUNS", "2")))

    def require_token(authorization: str | None = Header(default=None)) -> None:
        token = os.getenv("GEMCLI_SERVER_TOKEN")
        if not token:
            return
        supplied = (authorization or "").removeprefix("Bearer ").strip()
        if not hmac.compare_digest(supplied.encode(), token.encode()):
            raise HTTPException(status_code=401, detail="missing or invalid bearer token")

    @app.get("/healthz")
    def healthz() -> dict:
        return {
            "status": "ok",
            "workspace": str(config.workspace_root()),
            "approval_mode": config.approval_mode(),
        }

    @app.post("/analyze", dependencies=[Depends(require_token)])
    async def analyze(body: AnalyzeRequest) -> dict:
        async with limit:
            return await run_once(pipeline_factory(), body.request)

    return app


# Run with:  uvicorn server:create_app --factory --host 0.0.0.0 --port 8080

if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    uvicorn.run(create_app(), host=os.getenv("HOST", "127.0.0.1"), port=int(os.getenv("PORT", "8080")))
