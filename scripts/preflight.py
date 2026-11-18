#!/usr/bin/env python3
"""发布前阻断式校验（本地与 CI 共用同一道闸门）。

用法：
  python3 scripts/preflight.py skills/skill-web-html
  python3 scripts/preflight.py skills/skill-web-html --config publish.config.json --json
  python3 scripts/preflight.py skills/skill-web-html --version-source tag --tag v1.2.3
  python3 scripts/preflight.py skills/skill-web-html --strict   # 把警告升级为阻断

退出码：0 = 可发布，1 = 阻断，2 = 用法/环境错误。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib_frontmatter as fm  # noqa: E402
import policy  # noqa: E402

REFERENCE_RE = re.compile(r"(?:references|scripts|assets|templates)/[A-Za-z0-9._/\-]+")
REPO_ROOT = Path(__file__).resolve().parent.parent


def load_config(path: Path | None, slug_hint: str) -> dict:
    if not path or not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"配置文件解析失败: {exc}") from exc
    overrides = dict(data.get("overrides", {}))
    for item in data.get("skills", []):
        if item.get("path", "").rstrip("/").split("/")[-1] == slug_hint or item.get("slug") == slug_hint:
            overrides.update(item.get("overrides", {}))
            break
    return overrides


def check(skill_dir: Path, args, overrides: dict | None = None) -> dict:
    report: dict = {
        "skill_dir": str(skill_dir),
        "errors": [],
        "warnings": [],
        "files": [],
        "meta": {},
        "ok": False,
    }

    def err(code: str, msg: str) -> None:
        report["errors"].append({"code": code, "msg": msg})

    def warn(code: str, msg: str) -> None:
        report["warnings"].append({"code": code, "msg": msg})

    if not skill_dir.is_dir():
        err("dir-missing", f"目录不存在: {skill_dir}")
        return report

    if overrides is None:
        try:
            overrides = load_config(Path(args.config).resolve() if args.config else None,
                                    skill_dir.name)
        except ValueError as exc:
            err("config-invalid", str(exc))
            return report

    try:
        meta, body, meta_errors, meta_warnings = fm.resolve_meta(
            skill_dir, overrides, args.version_source, args.tag
        )
    except fm.FrontmatterError as exc:
        err("frontmatter-unreadable", str(exc))
        return report
    report["meta"] = {k: meta.get(k) for k in policy.REQUIRED_META}
    for item in meta_errors:
        err(item["code"], item["msg"])
    for item in meta_warnings:
        warn(item["code"], item["msg"])
    for item in fm.cross_check_with_yaml(skill_dir):
        err(item["code"], item["msg"])

    files = policy.collect_files(skill_dir)
    rel_names = [p.as_posix() for p in files]
    report["files"] = rel_names
    if policy.SKILL_MD not in rel_names:
        err("skill-md-missing", f"{policy.SKILL_MD} 不存在或被排除规则过滤掉了")
    if not files:
        err("empty-package", "过滤后发布包为空")
        return report

    total = 0
    for rel in files:
        full = skill_dir / rel
        size = full.stat().st_size
        total += size
        if size > policy.MAX_FILE_BYTES:
            err("file-size", f"{rel.as_posix()} 超过单文件上限 {policy.MAX_FILE_BYTES} 字节（{size}）")
        try:
            content = full.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            # 图片等二进制资源：只做体积检查，不做文本扫描
            continue
        for pattern, label in policy.SECRET_PATTERNS:
            if pattern.search(content):
                err("secret", f"{rel.as_posix()} 命中 {label}，发布前必须移除（公开平台上传后无法删除）")
                break
        for pattern, label in policy.LOCAL_PATH_PATTERNS:
            if pattern.search(content):
                warn("local-path", f"{rel.as_posix()} 含{label}，建议改成相对路径")
                break

    if total > policy.MAX_TOTAL_BYTES:
        err("total-size", f"整包 {total} 字节超过上限 {policy.MAX_TOTAL_BYTES}")

    # SKILL.md 引用了不会进包的文件 → 装完跑不起来。
    # 但若该路径在仓库根目录真实存在（例如文档里教开发者跑
    # `python3 scripts/publish.py --dry-run`），那是仓库级命令，不是包内资源，忽略。
    skill_text = (skill_dir / policy.SKILL_MD).read_text(encoding="utf-8")
    for ref in set(REFERENCE_RE.findall(skill_text)):
        if (skill_dir / ref).is_file():
            if ref not in rel_names:
                warn("referenced-but-excluded", f"SKILL.md 引用了 {ref}，但它被排除规则挡在包外")
        elif (REPO_ROOT / ref).exists():
            continue
        else:
            warn("broken-reference", f"SKILL.md 引用了不存在的文件: {ref}")

    # 提示：仓库里存在会被平台拒收的文件（已被排除，不阻断）
    for name in (".gitignore", "LICENSE", ".claude-plugin", ".github"):
        if (skill_dir / name).exists():
            warn("platform-rejected-file",
                 f"检测到 {name}，SkillHub 会返回 400；已自动排除，不会进发布包")

    if args.strict:
        for item in report["warnings"]:
            report["errors"].append(item)
        report["warnings"] = []

    report["ok"] = not report["errors"]
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description="SkillHub 发布前阻断式校验")
    ap.add_argument("skill_dir", help="Skill 目录（含 SKILL.md）")
    ap.add_argument("--config", help="publish.config.json 路径")
    ap.add_argument("--version-source", choices=["frontmatter", "tag", "env"],
                    default="frontmatter")
    ap.add_argument("--tag", help="version_source=tag 时的 git tag，例如 v1.2.3")
    ap.add_argument("--strict", action="store_true", help="把警告升级为阻断")
    ap.add_argument("--json", action="store_true", help="输出机读 JSON")
    args = ap.parse_args()

    report = check(Path(args.skill_dir).resolve(), args)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        label = report["meta"].get("slug") or Path(args.skill_dir).name
        print(f"=== preflight: {label} ===")
        print(f"文件数 {len(report['files'])} | "
              f"version={report['meta'].get('version', '-')} | "
              f"version_source={args.version_source}")
        for item in report["errors"]:
            print(f"  [BLOCK] {item['code']}: {item['msg']}")
        for item in report["warnings"]:
            print(f"  [warn ] {item['code']}: {item['msg']}")
        print("=> " + ("PASS 可以发布" if report["ok"] else "FAIL 已阻断发布"))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
