from __future__ import annotations

import argparse
import os
import secrets
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="生成 TokenLoom 管理员密钥")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists() and not args.force:
        print(f"管理员密钥已存在，未覆盖：{output}")
        return 0
    output.parent.mkdir(parents=True, exist_ok=True)
    value = "tloom_" + secrets.token_urlsafe(36)
    output.write_text(
        "TokenLoom 管理员密钥\n"
        "========================\n"
        f"{value}\n\n"
        "请妥善保存。登录时只输入上面以 tloom_ 开头的一整行；不要发送给他人。\n",
        encoding="utf-8",
    )
    try:
        os.chmod(output, 0o600)
    except OSError:
        pass
    print(f"管理员密钥已安全生成：{output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
