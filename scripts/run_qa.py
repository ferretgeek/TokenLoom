from __future__ import annotations

import base64
import json
import os
import secrets
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from argon2 import PasswordHasher

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    data_dir = ROOT / "data" / f"qa-{stamp}"
    data_dir.mkdir(parents=True, exist_ok=False)
    admin_key = "qa_" + secrets.token_urlsafe(24)
    admin_key_path = ROOT / "data" / f"qa-{stamp}-admin-key.txt"
    admin_key_path.write_text(admin_key + "\n", encoding="utf-8")
    try:
        os.chmod(admin_key_path, 0o600)
    except OSError:
        pass
    env = os.environ.copy()
    env.update(
        APP_ENV="test",
        DATABASE_URL=f"sqlite+aiosqlite:///{(ROOT / 'data' / f'qa-{stamp}.db').as_posix()}",
        ADMIN_KEY_HASH=PasswordHasher().hash(admin_key),
        SESSION_SECRET=secrets.token_urlsafe(48),
        ENCRYPTION_KEY=base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii"),
        DATA_DIR=str(data_dir),
        MIN_FREE_BYTES="0",
        TRUST_PROXY_HEADERS="false",
    )
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    logs = []
    processes = []
    commands = [
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            "8787",
            "--no-access-log",
        ],
        [sys.executable, "-m", "app.worker"],
    ]
    for index, command in enumerate(commands):
        out = (ROOT / "data" / f"qa-{stamp}-{index}.log").open("wb")
        logs.append(out)
        processes.append(
            subprocess.Popen(
                command,
                cwd=ROOT,
                env=env,
                stdout=out,
                stderr=subprocess.STDOUT,
                creationflags=flags,
            )
        )
    state = {"stamp": stamp, "pids": [process.pid for process in processes]}
    (ROOT / "data" / "qa-processes.json").write_text(json.dumps(state), encoding="utf-8")
    time.sleep(3)
    if any(process.poll() is not None for process in processes):
        for process in processes:
            if process.poll() is None:
                process.terminate()
        print("QA 服务启动失败，请检查 data/qa-*.log", file=sys.stderr)
        return 1
    print(
        f"QA 服务已启动：app_pid={processes[0].pid} worker_pid={processes[1].pid}；"
        f"临时管理员密钥保存在 {admin_key_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
