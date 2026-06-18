"""Shared pytest setup, loaded automatically before any test runs.

pytest imports every `conftest.py` it finds and adds that file's directory to
`sys.path`. Because this one lives in `backend/`, our test files can simply
`import pubmed` and `import main` without any path juggling.

API KEY HANDLING
----------------
`main.py` constructs the Anthropic client at import time (`client = Anthropic()`,
main.py:42), and that call raises if no key is set. For the fast unit tests the
client is mocked, so the key only needs to *exist*. But the end-to-end test makes
a REAL Claude call, so it needs the REAL key.

So we (1) load `backend/.env` first — which pulls in your real ANTHROPIC_API_KEY
(and NCBI_API_KEY) if present — then (2) `setdefault` a dummy only as a fallback
for environments that have no key at all (e.g. CI running just the unit tests).
Order matters: python-dotenv won't overwrite a var that already exists, so the
dummy must come *after* the real load.

TEST GATING
-----------
Integration and e2e tests are SKIPPED by default. Enable them explicitly:
    pytest --run-integration    # real PubMed/NCBI calls (free, network only)
    pytest --run-e2e            # real Claude API calls (COSTS MONEY)
    pytest --run-integration --run-e2e
Each flag also has an env-var equivalent (RUN_INTEGRATION=1, RUN_E2E=1) so CI can
turn them on without changing the command.
"""
import os
from pathlib import Path

import pytest

# 1. Load the real .env (real keys win), 2. dummy fallback if still unset.
try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass
os.environ.setdefault("ANTHROPIC_API_KEY", "test-dummy-key-not-real")


# ---- gating machinery for slow/costly tests --------------------------------

def pytest_addoption(parser):
    """Register the --run-integration / --run-e2e command-line flags."""
    parser.addoption(
        "--run-integration", action="store_true", default=False,
        help="run integration tests that hit real external services (PubMed/NCBI)",
    )
    parser.addoption(
        "--run-e2e", action="store_true", default=False,
        help="run end-to-end tests that call the real Claude API (costs money)",
    )


def pytest_configure(config):
    """Register our custom markers so pytest doesn't warn about them."""
    config.addinivalue_line(
        "markers", "integration: hits real external services (e.g. PubMed); opt-in",
    )
    config.addinivalue_line(
        "markers", "e2e: full end-to-end test calling the real Claude API; opt-in, costs money",
    )


def _truthy_env(name):
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def pytest_collection_modifyitems(config, items):
    """Auto-skip integration/e2e tests unless their flag (or env var) is set."""
    run_integration = config.getoption("--run-integration") or _truthy_env("RUN_INTEGRATION")
    run_e2e = config.getoption("--run-e2e") or _truthy_env("RUN_E2E")

    skip_integration = pytest.mark.skip(reason="needs --run-integration (or RUN_INTEGRATION=1)")
    skip_e2e = pytest.mark.skip(reason="needs --run-e2e (or RUN_E2E=1)")

    for item in items:
        if "e2e" in item.keywords and not run_e2e:
            item.add_marker(skip_e2e)
        elif "integration" in item.keywords and not run_integration:
            item.add_marker(skip_integration)
