"""A stand-in for the `gemini` executable, so tests and the offline demo need no CLI, login or key.

It imitates the headless interface the tool relies on:  gemini -p "<text>" --output-format json
(the real task arrives on stdin). Behaviour is chosen with FAKE_GEMINI_MODE:

  ok (default)  JSON {"response": ..., "stats": ...}; the response echoes what it was called with
  plain         non-JSON text on stdout
  noisy         log lines before the JSON object
  apierror      JSON {"error": {...}} and exit 1
  exit42        invalid-arguments exit code, message on stderr
  crash         exit 1 with a stack-trace-like stderr
  hang          sleeps forever (timeout tests)
  huge          a very large response
  sleepy        waits 0.3 s then answers (concurrency checks)
  listing       (offline demo) answers with a real listing of the files in its working directory
"""

import json
import os
import sys
import time

mode = os.environ.get("FAKE_GEMINI_MODE", "ok")
task = sys.stdin.read()


def emit(obj) -> None:
    sys.stdout.write(json.dumps(obj))
    sys.stdout.flush()


if mode == "hang":
    time.sleep(3600)
elif mode == "exit42":
    sys.stderr.write("Error: invalid arguments\n")
    sys.exit(42)
elif mode == "crash":
    sys.stderr.write("Traceback (most recent call last):\n  RuntimeError: boom\n")
    sys.exit(1)
elif mode == "apierror":
    emit({"error": {"type": "ApiError", "message": "quota exceeded", "code": 429}})
    sys.exit(1)
elif mode == "plain":
    print("just some plain text, not JSON")
elif mode == "huge":
    emit({"response": "x" * 500_000, "stats": {}})
elif mode == "noisy":
    print("Loaded cached credentials.")
    emit({"response": "answer after log noise", "stats": {}})
elif mode == "listing":
    rows = []
    for dirpath, dirnames, filenames in os.walk("."):
        dirnames[:] = [d for d in dirnames if d != ".git"]
        for f in sorted(filenames):
            path = os.path.join(dirpath, f)
            with open(path, encoding="utf-8", errors="replace") as fh:
                rows.append(f"- {os.path.relpath(path).replace(os.sep, '/')}: {sum(1 for _ in fh)} lines")
    header = "(offline demo: scripted stand-in for Gemini CLI, not a model)" + chr(10) + "Files found:" + chr(10)
    emit({"response": header + chr(10).join(rows), "stats": {}})
else:
    if mode == "sleepy":
        time.sleep(0.3)
    emit(
        {
            "response": "FAKE GEMINI ANSWER\n"
            f"task: {task}\n"
            f"cwd: {os.path.basename(os.getcwd())}\n"
            f"argv: {sys.argv[1:]}\n"
            f"secret_visible: {'SUPER_SECRET_TOKEN' in os.environ}\n",
            "stats": {"models": {"gemini-fake": {"api": {"totalRequests": 1, "totalLatencyMs": 12},
                                                   "tokens": {"total": 42}}}},
        }
    )
