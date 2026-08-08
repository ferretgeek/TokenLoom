from __future__ import annotations

import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = (ROOT / "data").resolve()


def main() -> int:
    DATA.mkdir(parents=True, exist_ok=True)
    removed = 0
    for target in DATA.iterdir():
        if not (
            target.name.startswith("qa-")
            or (target.name.startswith("refresh-admin-") and target.name.endswith(".tar.gz"))
        ):
            continue
        resolved = target.resolve()
        if resolved.parent != DATA:
            raise RuntimeError(f"拒绝清理越界路径：{resolved}")
        if resolved.is_dir():
            shutil.rmtree(resolved)
        else:
            resolved.unlink()
        removed += 1
    cache_targets = [ROOT / ".pytest_cache", ROOT / ".ruff_cache"]
    cache_targets.extend(
        path
        for path in ROOT.rglob("__pycache__")
        if ".venv" not in path.relative_to(ROOT).parts and ".git" not in path.relative_to(ROOT).parts
    )
    caches_removed = 0
    for target in sorted(set(cache_targets), key=lambda path: len(path.parts), reverse=True):
        resolved = target.resolve()
        if ROOT not in resolved.parents or not resolved.exists():
            continue
        shutil.rmtree(resolved)
        caches_removed += 1
    print(f"已清理 {removed} 个 QA/部署临时项与 {caches_removed} 个代码缓存目录")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
