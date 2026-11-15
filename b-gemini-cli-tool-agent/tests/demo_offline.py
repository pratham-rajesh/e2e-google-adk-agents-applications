"""Scripted LLMs + the fake `gemini` executable, used by `scripts/run_pipeline.py --offline`.

Only the LLM replies and the CLI binary are stand-ins. ADK's runner, state, tool dispatch, the
workspace sandbox and the real subprocess plumbing of the gemini_cli tool all run for real.
"""

import json
import os
import shutil
import sys
from pathlib import Path

from gemini_cli_agent import config
from gemini_cli_agent.pipeline import Models, build_pipeline

from .fake_llm import FakeLlm, call

HERE = Path(__file__).resolve().parent
SAMPLE = HERE.parent / "examples" / "sample_project"

PLAN = """GOAL: Give an inventory of the files in the sample project.
REPOSITORY: none
CLI TASK: List every file in this directory with a one-line description of its purpose. Cite file paths.
SUCCESS CRITERIA:
- every file in the directory is mentioned
- no claims about files that are not there"""


def build_offline_agent():
    """Copies the sample project into the workspace and points GEMCLI_BIN at the fake executable."""
    os.environ["GEMCLI_BIN"] = json.dumps([sys.executable, str(HERE / "fake_gemini.py")])
    os.environ["FAKE_GEMINI_MODE"] = "listing"
    if "FAKE_GEMINI_MODE" not in config.ENV_PASSTHROUGH:  # demo only; production never forwards it
        config.ENV_PASSTHROUGH = (*config.ENV_PASSTHROUGH, "FAKE_GEMINI_MODE")
    dest = config.workspace_root() / "sample_project"
    shutil.copytree(SAMPLE, dest, dirs_exist_ok=True)

    m = Models(
        planner=FakeLlm(script=[PLAN]),
        worker=FakeLlm(script=[
            call("list_workspace", subdirectory="."),
            call("gemini_cli", task="List every file in this directory with a one-line description of its "
                                    "purpose. Cite file paths.", subdirectory="sample_project"),
            "Gemini CLI inventory (relayed unchanged):\nSee the call log; both files were listed.\n"
            "Source: Gemini CLI, sample_project",
        ]),
        reviewer=FakeLlm(script=[
            "VERDICT: PASS\nCHECKS: every file mentioned - met (app.py, README.md); no extra files - met\n"
            "ISSUES: none (offline demo: the CLI was a scripted stand-in)\n"
            "FINAL ANSWER: sample_project contains app.py and README.md (see call log for line counts)."
        ]),
    )
    return build_pipeline(m)
