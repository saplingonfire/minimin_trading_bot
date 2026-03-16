"""Pytest conftest: add repo root to path so bot and config are importable."""

import os
import sys

# Repo root (parent of tests/)
_repo_root = os.path.dirname(os.path.abspath(os.path.dirname(__file__)))
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)
