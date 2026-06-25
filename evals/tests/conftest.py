"""Put the evals/ root on sys.path so tests can import the eval modules
(`validate_dataset`, `_pathsetup`) by name when pytest is run from the repo root.
"""
import os
import sys

_EVALS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _EVALS not in sys.path:
    sys.path.insert(0, _EVALS)
