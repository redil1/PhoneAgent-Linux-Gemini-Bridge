"""Run offline pytest without using the operator's PhoneAgent home state.

Run with the checkout's .venv/bin/python and pass ordinary pytest arguments.
The temporary root is process-local: HOME and the real user files are unchanged.
"""

from __future__ import annotations

import os
import pathlib
import sys
import tempfile


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="phoneagent-test-user-") as test_root:
        # Set before importing pytest/conftest/application modules because many
        # configuration defaults bind Path.home() once at import time.
        pathlib.Path.home = classmethod(lambda cls: cls(test_root))
        for name in list(os.environ):
            if name.startswith("PHONE_AGENT_"):
                os.environ.pop(name)
        import pytest

        return int(pytest.main(sys.argv[1:] or ["-q", "--tb=short"]))


if __name__ == "__main__":
    raise SystemExit(main())
