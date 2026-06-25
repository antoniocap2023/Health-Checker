"""Put the backend package on sys.path so eval scripts can import its modules.

The eval suite is a top-level package that *reuses* the backend (pubmed.py,
config.py, storage.py, ...) rather than duplicating it. Importing this module
(``import _pathsetup``) prepends ``../backend`` to sys.path as a side effect, so a
later ``import pubmed`` resolves. Scripts not sitting directly in ``evals/`` must
first add ``evals/`` to the path themselves before importing this.
"""
import os
import sys

_BACKEND = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend"
)
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

# Eval scripts run from the repo root, but the backend's config (`config.py`) reads
# `.env` relative to the CWD — so it would MISS backend/.env and run keyless. That
# matters a lot for NCBI: no NCBI_API_KEY means the 2/sec keyless cap (and 429s)
# instead of 9/sec. Load backend/.env into the environment here, before config is
# imported, so every eval script inherits the real key (and ANTHROPIC_API_KEY, AWS
# profile, etc.). override=False keeps any value already set in the environment.
try:
    from dotenv import load_dotenv

    load_dotenv(os.path.join(_BACKEND, ".env"), override=False)
except ImportError:
    pass
