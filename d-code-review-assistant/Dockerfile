# Service image: FastAPI + ADK pipelines. UNVERIFIED locally: the Docker daemon was not running on the author's
# machine, so this was not built (see README "Verified / not verified").
#   docker build -t code-review-assistant .
#   docker run -p 8080:8080 -e GOOGLE_API_KEY=... -e CODEREVIEW_SERVER_TOKEN=... code-review-assistant
FROM python:3.12-slim

RUN useradd --create-home --uid 10001 agent
WORKDIR /app

COPY pyproject.toml ./
RUN pip install --no-cache-dir "google-adk>=1.18" "pydantic>=2.7" "python-dotenv>=1.0" "pytest>=8" \
    "fastapi>=0.110" "uvicorn>=0.30"

COPY code_review_assistant ./code_review_assistant
COPY server.py ./
RUN chown -R agent:agent /app
USER agent

# SECURITY: this image does NOT execute submitted code by default. The service holds API credentials (and, on Cloud
# Run, can reach the metadata server), and a plain subprocess is not a security boundary. Switch the sandbox on only
# when the executor is isolated from those: CODEREVIEW_SANDBOX=docker needs a Docker host (not available inside Cloud
# Run); on GCP use a separate no-egress executor (see README "Deploying").
ENV CODEREVIEW_SANDBOX=off \
    CODEREVIEW_ARTIFACT_DIR=/app/artifacts \
    HOST=0.0.0.0 PORT=8080
EXPOSE 8080
# Provide GOOGLE_API_KEY (or Vertex settings) and CODEREVIEW_SERVER_TOKEN at run time; never bake them in.
CMD ["sh", "-c", "uvicorn server:create_app --factory --host 0.0.0.0 --port ${PORT}"]
