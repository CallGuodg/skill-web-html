#!/usr/bin/env python3
"""生成「干净发布副本」：按 policy 过滤文件，并把 frontmatter 重写为平台兼容格式。

关键点：绝不修改源 Skill 目录。GitHub 仓库里 .github/、.gitignore、无扩展名
LICENSE、publish.config.json 等会被 SkillHub 返回 400，源目录里可以放心保留，
发布时只上传这个临时副本。

用法：
  python3 scripts/stage.py skills/skill-web-html --out /tmp/stage/skill-web-html
  python3 scripts/stage.py skills/skill-web-html --config publish.config.json --version-source tag --tag v1.2.3
"""
from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib_frontmatter as fm  # noqa: E402
import policy  # noqa: E402
from preflight import check, load_config  # noqa: E402


class _Args:
    """复用 preflight.check 所需的最小参数集。"""


def stage(skill_dir: Path, out_dir: Path | None, config: str | None,
          version_source: str, tag: str | None, strict: bool = False,
          overrides: dict | None = None) -> Path:
    args = _Args()
    args.config = config
    args.version_source = version_source
    args.tag = tag
    args.strict = strict

    # 传入 overrides 时，校验与打包共用调用方算好的同一份元数据
    if overrides is None:
        overrides = load_config(Path(config).resolve() if config else None, skill_dir.name)

    report = check(skill_dir, args, overrides)
    if not report["ok"]:
        detail = "; ".join(f"{x['code']}: {x['msg']}" for x in report["errors"])
        raise RuntimeError("preflight 未通过，拒绝打包 -> " + detail)

    target = out_dir or Path(tempfile.mkdtemp(prefix="skillhub-stage-"))
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)

    meta, body, _, _ = fm.resolve_meta(skill_dir, overrides, version_source, tag)

    for rel in policy.collect_files(skill_dir):
        src = skill_dir / rel
        dst = target / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        if rel.as_posix() == policy.SKILL_MD:
            dst.write_text(fm.render(meta, body), encoding="utf-8")
        else:
            shutil.copy2(src, dst)

    return target


def main() -> int:
    ap = argparse.ArgumentParser(description="生成 SkillHub 干净发布副本")
    ap.add_argument("skill_dir")
    ap.add_argument("--out", help="输出目录；不指定则创建临时目录")
    ap.add_argument("--config", help="publish.config.json 路径")
    ap.add_argument("--version-source", choices=["frontmatter", "tag", "env"],
                    default="frontmatter")
    ap.add_argument("--tag")
    ap.add_argument("--strict", action="store_true")
    args = ap.parse_args()
    try:
        out = stage(Path(args.skill_dir).resolve(),
                    Path(args.out).resolve() if args.out else None,
                    args.config, args.version_source, args.tag, args.strict)
    except (RuntimeError, fm.FrontmatterError) as exc:
        print(f"[stage] 失败: {exc}", file=sys.stderr)
        return 1
    print(out.as_posix())
    return 0


if __name__ == "__main__":
    sys.exit(main())
