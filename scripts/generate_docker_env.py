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
    parser = argparse.ArgumentParser(description="生成 TokenLoom Docker 本地部署配置")
    parser.add_argument("--admin-key-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path(".env"))
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    output = args.output.resolve()
    if output.exists() and not args.force:
        raise FileExistsError(f"配置文件已存在，拒绝覆盖：{output}")
    admin_key = read_admin_key(args.admin_key_file.resolve())
    values = {
        "POSTGRES_PASSWORD": secrets.token_urlsafe(32),
        "ADMIN_KEY_HASH": PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2).hash(admin_key),
        "SESSION_SECRET": secrets.token_urlsafe(48),
        "ENCRYPTION_KEY": base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii"),
        "TOKEN_LOOM_PORT": "8787",
        "COOKIE_SECURE": "false",
        "TRUST_PROXY_HEADERS": "false",
        "TRUSTED_PROXY_IPS": "127.0.0.1,::1",
        "ALLOWED_HOSTS": "localhost,127.0.0.1,::1",
        "MIN_FREE_BYTES": "268435456",
    }
    output.write_text(
        "".join(f"{key}={value}\n" for key, value in values.items()), encoding="utf-8", newline="\n"
    )
    try:
        os.chmod(output, 0o600)
    except OSError:
        pass
    print(f"Docker 配置已生成：{output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
