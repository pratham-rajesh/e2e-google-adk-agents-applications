"""HTTP service around the assistant (FastAPI), the thing the Dockerfile runs.

  POST /review   {"code": "...", "fix": false}  -> verdict, report, (fix outcome), artifact versions
  GET  /healthz                                                      sandbox mode, limits
  /run, /run_sse, /list-apps, ...   the standard ADK API server (adk api_server), mounted when with_adk_api=True

Set CODEREVIEW_SERVER_TOKEN to require `Authorization: Bearer <token>` on /review. Every request is an LLM run plus
a sandboxed code execution, so a public deployment must never be anonymous.
"""

from __future__ import annotations

import asyncio
import hmac
import os
from pathlib import Path
from typing import Callable

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from code_review_assistant import config
from code_review_assistant.runner import run_review

ROOT = Path(__file__).resolve().parent


class ReviewRequest(BaseModel):
    code: str = Field(min_length=1, description="Python source to review")
    fix: bool = Field(default=False, description="After the review, produce and validate a fix")


def create_app(agent_factory: Callable | None = None, with_adk_api: bool = True) -> FastAPI:
    if with_adk_api:
        from google.adk.cli.fast_api import get_fast_api_app

        app = get_fast_api_app(agents_dir=str(ROOT), web=os.getenv("CODEREVIEW_WEB_UI") == "1")
    else:
        app = FastAPI()
    app.title = "Code review assistant"

    if agent_factory is None:
        from code_review_assistant.pipeline import build_root_agent as agent_factory  # noqa: N813

    limit = asyncio.Semaphore(int(os.getenv("CODEREVIEW_MAX_CONCURRENT_RUNS", "2")))

    def require_token(authorization: str | None = Header(default=None)) -> None:
        token = config.server_token()
        if not token:
            return
        supplied = (authorization or "").removeprefix("Bearer ").strip()
        if not hmac.compare_digest(supplied.encode(), token.encode()):
            raise HTTPException(status_code=401, detail="missing or invalid bearer token")

    @app.get("/healthz")
    def healthz() -> dict:
        return {"status": "ok", "sandbox": config.sandbox_mode(), "max_code_chars": config.max_code_chars(),
                "fix_max_iterations": config.fix_max_iterations()}

    @app.post("/review", dependencies=[Depends(require_token)])
    async def review(body: ReviewRequest) -> dict:
        if len(body.code) > config.max_code_chars():
            raise HTTPException(status_code=413, detail=f"code is larger than {config.max_code_chars()} characters")
        async with limit:
            out = await run_review(agent_factory(), body.code, fix=body.fix)
        return {"verdict": (out["report"] or {}).get("verdict"), "report": out["report"], "fix": out["fix"],
                "artifacts": out["artifacts"], "answer": out["answer"]}

    return app


# Run with:  uvicorn server:create_app --factory --host 0.0.0.0 --port 8080

if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    uvicorn.run(create_app(), host=os.getenv("HOST", "127.0.0.1"), port=int(os.getenv("PORT", "8080")))
