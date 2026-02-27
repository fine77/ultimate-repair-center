from __future__ import annotations

import os
import subprocess
from typing import Tuple


def run_action(action: str, apply: bool = False) -> Tuple[int, str, str]:
    # Restore-only integration hook. Can point to local ops-repair wrapper.
    cmd = [os.getenv("URC_EXECUTOR_CMD", "/bin/echo"), f"action={action}"]
    if apply:
        cmd.append("apply=true")
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    return proc.returncode, proc.stdout, proc.stderr
