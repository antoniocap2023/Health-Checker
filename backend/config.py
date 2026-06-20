"""Central configuration for the backend — one typed place for every setting.

Before this module, configuration was scattered: ``os.environ.get(...)`` calls
in main.py, pubmed.py, and deep_research.py, plus magic numbers inline. That makes
it hard to see what's tunable and impossible to tell at a glance how a dev
environment should differ from production. Now there is ONE answer: this file.

We use pydantic-settings (``BaseSettings``). Each field below becomes:
  - a typed attribute with a default (so the app runs with zero env vars), and
  - an override read from an environment variable of the SAME NAME, upper-cased.
    e.g. the field ``max_tokens`` is overridden by the env var ``MAX_TOKENS``;
    ``ncbi_api_key`` by ``NCBI_API_KEY``. Types are validated and coerced, so
    ``MAX_TOKENS=8192`` arrives as the int 8192, not the string "8192".

This is exactly the seam we need for AWS: dev and prod run the SAME code and
differ only in the environment variables their containers are given.

Import the shared instance everywhere:  ``from config import settings``
"""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Read overrides from the process environment and, as a convenience for local
    # runs, from a .env file in the working directory. Unknown env vars are
    # ignored rather than raising, so the host can carry other variables freely.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # ---- Logging -----------------------------------------------------------
    # Verbosity for OUR loggers (the "healthchecker.*" tree). DEBUG also dumps
    # full tool payloads and answer text; INFO stays scannable.
    log_level: str = "INFO"

    # ---- Anthropic / main agent loop ---------------------------------------
    # The model that drives the conversation and decides when to call tools.
    model: str = "claude-opus-4-8"
    # Hard ceiling on tokens the model may write in one turn. Too low truncates a
    # rich answer mid-sentence; deep_research synthesis needs the headroom.
    max_tokens: int = 4096
    # Max PubMed/deep-research tool calls one answer may make (across all turns).
    # A bound on the agentic loop and cost — NOT a rate guard (that's the limiter).
    max_tool_calls: int = 12
    # Style nudge that urges shorter, plainer answers (separate from max_tokens).
    concise_mode: bool = True

    # ---- HTTP / CORS -------------------------------------------------------
    # Browser origins allowed to call this API. Locally that's the Vite dev
    # server; in dev/prod on AWS this becomes the deployed frontend's URL.
    cors_allow_origins: list[str] = ["http://localhost:5173"]

    # ---- NCBI / PubMed rate limiting ---------------------------------------
    # NCBI's E-utilities key raises the per-IP cap from ~3 to ~10 req/sec. Read by
    # pubmed.py; also the secret the future pubmed-proxy service will own.
    ncbi_api_key: str | None = None
    # The sliding window the limiter enforces (requests per this many seconds).
    ncbi_window_seconds: float = 1.0
    # Reactive retry policy for transient NCBI failures (429/5xx/network blips).
    ncbi_max_retries: int = 4
    ncbi_backoff_base: float = 0.5

    # ---- deep_research sub-agents ------------------------------------------
    # A cheaper/faster model is plenty for reading one paper and extracting findings.
    deep_research_model: str = "claude-sonnet-4-6"
    deep_research_max_papers: int = 6      # cap fan-out per deep_research call
    deep_research_max_workers: int = 4     # concurrent sub-agents (Anthropic calls)
    deep_research_max_tokens: int = 2048   # output ceiling per sub-agent
    deep_research_char_cap: int = 120000   # truncate huge papers before sending

    @property
    def ncbi_rate_limit(self) -> int:
        """Max requests per window. With an API key NCBI allows ~10/sec; we stay
        one under as a safety margin. Without a key the cap is ~3/sec, so use 2."""
        return 9 if self.ncbi_api_key else 2


# The single shared settings instance the whole app imports. Built once at import
# time from the environment; treat it as read-only at runtime (tests override
# individual fields via monkeypatch, which is why it's a mutable instance).
settings = Settings()
