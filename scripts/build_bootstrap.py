from __future__ import annotations

import argparse
import base64
import os
import secrets
from pathlib import Path

from argon2 import PasswordHasher


def read_admin_key(path: Path) -> str:
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("tloom_"):
            return line.strip()
    raise ValueError("管理员密钥文件里没有找到 tloom_ 开头的密钥")


def main() -> int:
    parser = argparse.ArgumentParser(description="生成一次性服务器部署密钥")
    parser.add_argument("--admin-key-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--force", action="store_true", help="明确覆盖已有的一次性密钥文件")
    args = parser.parse_args()
    admin_key = read_admin_key(args.admin_key_file.resolve())
    output = args.output.resolve()
    if output.exists() and not args.force:
        raise FileExistsError(f"部署密钥文件已存在，拒绝覆盖：{output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    values = {
        "DB_PASSWORD": secrets.token_urlsafe(32),
        "ADMIN_KEY_HASH": PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2).hash(admin_key),
        "SESSION_SECRET": secrets.token_urlsafe(48),
        "ENCRYPTION_KEY": base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii"),
    }
    # Force LF even when this script runs on Windows. A stray CR becomes part of
    # a sourced shell value and can silently corrupt DATABASE_URL.
    output.write_bytes("".join(f"{key}='{value}'\n" for key, value in values.items()).encode("utf-8"))
    try:
        os.chmod(output, 0o600)
    except OSError:
        pass
    print(f"一次性部署密钥已生成：{output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
