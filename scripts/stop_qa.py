from __future__ import annotations

import json
import os
import signal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    state_path = ROOT / "data" / "qa-processes.json"
    if not state_path.exists():
        print("没有记录中的 QA 进程")
        return 0
    state = json.loads(state_path.read_text(encoding="utf-8"))
    for pid in state.get("pids", []):
        try:
            os.kill(int(pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
    state_path.unlink(missing_ok=True)
    print("QA 进程已停止")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
