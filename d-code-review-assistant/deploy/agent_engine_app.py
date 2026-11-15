"""Deploy the root agent to Vertex AI Agent Engine. UNVERIFIED: needs a billed Google Cloud project and the
`google-cloud-aiplatform[agent_engines,adk]` package, neither of which exists in the author's free-tier setup, so
this has never been executed. The ADK/Agent Engine API names below follow the public docs at the time of writing; check
them against the current docs before use.

    pip install "google-cloud-aiplatform[agent_engines,adk]"
    python deploy/agent_engine_app.py --project my-project --location us-central1 --bucket gs://my-staging-bucket

Agent Engine runs the agent in a managed runtime, with sessions and artifacts handled by managed services instead of the
in-memory ones used locally. The sandbox should stay `off` there, or use Agent Engine's code-execution sandbox instead
of this repo's subprocess/docker runner.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def deploy(project: str, location: str, bucket: str) -> None:
    import vertexai
    from vertexai import agent_engines

    from code_review_assistant.pipeline import build_root_agent

    vertexai.init(project=project, location=location, staging_bucket=bucket)
    app = agent_engines.AdkApp(agent=build_root_agent(), enable_tracing=True)
    remote = agent_engines.create(
        app,
        display_name="code-review-assistant",
        requirements=["google-cloud-aiplatform[agent_engines,adk]", "pydantic>=2.7", "pytest>=8"],
        extra_packages=["./code_review_assistant"],
        env_vars={"CODEREVIEW_SANDBOX": "off"},
    )
    print("Deployed:", remote.resource_name)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--location", default="us-central1")
    ap.add_argument("--bucket", required=True)
    a = ap.parse_args()
    deploy(a.project, a.location, a.bucket)
