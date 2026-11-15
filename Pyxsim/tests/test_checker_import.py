# Copyright 2026 XMOS LIMITED.
# This Software is subject to the terms of the XMOS Public Licence: Version 1.
"""The output checkers and Pyxrun are usable without XTC Tools: importing them must not load XSI."""

import os
import subprocess
import sys


def test_checker_and_hardware_imports_do_not_load_xsi():
    env = os.environ.copy()
    env.pop("XCC_EXEC_PREFIX", None)
    result = subprocess.run(
        [sys.executable, "-c", "import sys, Pyxrun; from Pyxsim.testers import ComparisonTester; "
         "assert 'Pyxsim.pyxsim' not in sys.modules"],
        env=env, capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr
