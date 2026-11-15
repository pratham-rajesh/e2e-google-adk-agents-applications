# Gemini CLI (Node) + ADK pipeline (Python) in one image. UNVERIFIED locally: not built on the
# author's machine to save disk space (see docs/handover/part-b.md). Build: docker build -t gemini-cli-agent .
FROM python:3.12-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates git \
 && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
 && apt-get install -y --no-install-recommends nodejs \
 && npm install -g @google/gemini-cli \
 && rm -rf /var/lib/apt/lists/* /root/.npm

# Run as a non-root user: Gemini CLI reads the workspace and must not own the container.
RUN useradd --create-home --uid 10001 agent
WORKDIR /app

COPY pyproject.toml ./
RUN pip install --no-cache-dir "google-adk>=1.18" "pydantic>=2.7" "python-dotenv>=1.0"

COPY gemini_cli_agent ./gemini_cli_agent
COPY workspace_template ./workspace_template
COPY server.py ./

RUN mkdir -p /app/workspace /app/output && chown -R agent:agent /app
USER agent

ENV GEMCLI_WORKSPACE=/app/workspace \
    GEMCLI_OUTPUT_DIR=/app/output \
    HOST=0.0.0.0 PORT=8080

EXPOSE 8080
# Provide GOOGLE_API_KEY / GEMINI_API_KEY (and GEMCLI_SERVER_TOKEN) at run time; never bake them in.
CMD ["sh", "-c", "uvicorn server:create_app --factory --host 0.0.0.0 --port ${PORT}"]
