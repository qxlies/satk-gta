"""Finite-lived, hidden descendants for MCP cleanup tests; writes only PID fixtures."""

import os
import subprocess
import sys
import time
from pathlib import Path


if __name__ == "__main__":
    root, role = Path(sys.argv[1]), sys.argv[2]
    (root / f"{role}.pid").write_text(str(os.getpid()), encoding="ascii")
    if role == "child":
        descendant = subprocess.Popen(
            [sys.executable, __file__, str(root), "grandchild"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    time.sleep(30)
