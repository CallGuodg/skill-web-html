#!/usr/bin/env python3
"""GitHub -> SkillHub 发布编排器（本地与 CI 共用同一条路径）。

流程：扫描/读取 skills -> preflight 阻断校验 -> 生成干净副本 ->
     skillhub login / auth whoami -> publish --dry-run -> publish 正式发布

安全约定：
  * Token 只从环境变量 SKILLHUB_KEY 读取，绝不写入文件、绝不回显到日志。
  * --dry-run 时全程不接触凭证，可在 Pull Request 里安全运行。
  * 单个 Skill 失败不阻断其余，最终汇总并给出非零退出码（便于幂等重跑）。

用法：
  python3 scripts/publish.py --dry-run
  python3 scripts/publish.py --only skill-web-html
  python3 scripts/publish.py --version-source tag --tag v1.2.3
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib_frontmatter as fm  # noqa: E402
import policy  # noqa: E402
from stage import stage  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_HOST = "https://api.skillhub.cn"
RETRYABLE = ("502", "503", "504", "Bad Gateway", "timed out", "Connection reset", "temporarily")


def log(msg: str) -> None:
    print(f"[publish] {msg}", flush=True)


def mask(text: str) -> str:
    token = os.environ.get("SKILLHUB_KEY", "")
    return text.replace(token, "***SKILLHUB_KEY***") if token else text


def run(cmd: list[str], *, cwd: Path | None = None, check: bool = False,
        capture: bool = True, quiet: bool = False) -> subprocess.CompletedProcess:
    """执行外部命令，日志一律脱敏。"""
    shown = " ".join(mask(c) for c in cmd)
    if not quiet:
        log(f"$ {shown}")
    proc = subprocess.run(
        cmd, cwd=str(cwd or ROOT), check=False,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.STDOUT if capture else None,
        text=True, encoding="utf-8", errors="replace",
    )
    out = mask(proc.stdout or "")
    if capture and not quiet and out.strip():
        print(out.rstrip(), flush=True)
    if check and proc.returncode != 0:
        raise RuntimeError(f"命令失败 (exit {proc.returncode}): {shown}")
    return proc


def find_cli() -> str:
    """定位 skillhub CLI。非交互 shell 不会加载 ~/.zshrc，必须显式补 PATH。"""
    local = Path.home() / ".local" / "bin" / "skillhub"
    if local.is_file():
        return str(local)
    found = shutil.which("skillhub")
    if found:
        return found
    raise RuntimeError(
        "未找到 skillhub CLI。请先安装："
        "curl -fsSL https://skillhub.cn/install/install.sh | bash -s -- --cli-only"
    )


def semver_key(v: str) -> tuple[int, int, int]:
    m = re.match(r"(\d+)\.(\d+)\.(\d+)", v or "")
    return tuple(int(x) for x in m.groups()) if m else (0, 0, 0)  # type: ignore[return-value]


def old_version_from_ref(skill_rel: str, base_ref: str) -> str | None:
    """从 base_ref 里取出同一 Skill 的上一版 version，用于「必须递增」校验。"""
    proc = run(["git", "show", f"{base_ref}:{skill_rel}/{policy.SKILL_MD}"],
               capture=True, quiet=True)
    if proc.returncode != 0:
        return None
    try:
        meta, _, _ = fm.parse(proc.stdout or "")
        return str(meta.get("version") or "") or None
    except fm.FrontmatterError:
        return None


def make_changelog(changelog: str | None, version: str, base_ref: str | None,
                   skill_rel: str) -> str:
    if changelog:
        return changelog
    if base_ref:
        proc = run(["git", "log", "--no-merges", "--pretty=format:%s",
                    f"{base_ref}..HEAD", "--", skill_rel], capture=True, quiet=True)
        if proc.returncode == 0:
            subjects = [x.strip() for x in (proc.stdout or "").splitlines() if x.strip()]
            if subjects:
                return " | ".join(subjects[:8])
    return f"Release {version}"


def load_config(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"publish.config.json 解析失败: {exc}") from exc


def discover_skills(cfg: dict) -> list[dict]:
    """config 优先；未列出时自动扫描 skills/ 下所有含 SKILL.md 的目录。

    全局 overrides 与单个 skill 的 overrides 在这里合并，确保 publish.py 与
    stage.py / preflight.py 算出的 version/slug 完全一致。
    """
    global_overrides = dict(cfg.get("overrides", {}))
    items = cfg.get("skills") or []
    if items:
        merged = []
        for x in items:
            ov = dict(global_overrides)
            ov.update(x.get("overrides", {}))
            merged.append({"path": x["path"],
                           "slug": x.get("slug") or Path(x["path"]).name,
                           "overrides": ov})
        return merged
    base = ROOT / "skills"
    if not base.is_dir():
        return []
    return [{"path": f"skills/{p.name}", "slug": p.name,
             "overrides": dict(global_overrides)}
            for p in sorted(base.iterdir()) if (p / policy.SKILL_MD).is_file()]


def check_version_bump(skill_rel: str, new_version: str, base_ref: str | None) -> None:
    if not base_ref:
        return
    old = old_version_from_ref(skill_rel, base_ref)
    if not old:
        log(f"base_ref={base_ref} 中无历史版本，跳过版本递增校验")
        return
    if semver_key(new_version) <= semver_key(old):
        raise RuntimeError(
            f"版本未递增：{old} -> {new_version}。SkillHub 要求更新时保持 slug 不变并递增 version，"
            "否则会覆盖失败或仍返回旧包"
        )
    log(f"版本递增校验通过：{old} -> {new_version}")


def login(cli: str, host: str, token: str) -> None:
    run([cli, "login", "--key", token, "--host", host], check=True)
    who = run([cli, "auth", "whoami"], check=False)
    if who.returncode != 0:
        log("whoami 失败，尝试旧版登录命令 skillhub auth login --token")
        run([cli, "auth", "login", "--token", token, "--host", host], check=True)
        who = run([cli, "auth", "whoami"], check=True)
    expected = os.environ.get("SKILLHUB_PUBLISHER", "").strip()
    if expected:
        if expected not in (who.stdout or ""):
            raise RuntimeError(
                f"登录身份与 SKILLHUB_PUBLISHER={expected!r} 不符，已停止发布以防发错账号"
            )
        log(f"身份核对通过：{expected}")


def publish_one(cli: str, host: str, staged: Path, changelog: str,
                dry_run: bool) -> tuple[bool, str]:
    base = [cli, "publish", str(staged), "--host", host]
    probe = run(base + ["--dry-run"])
    if probe.returncode != 0:
        return False, "dry-run 未通过（格式或打包问题）"

    if dry_run:
        return True, "dry-run passed（未提交发布）"

    cmd = base + ["--changelog", changelog]
    for attempt in range(3):
        proc = run(cmd)
        out = proc.stdout or ""
        if proc.returncode == 0:
            m = re.search(r"skillId=([^\s]+)\s+status=([^\s]+)", out)
            status = f"skillId={m.group(1)} status={m.group(2)}" if m else "已提交"
            return True, status
        if attempt < 2 and any(x in out for x in RETRYABLE):
            wait = 5 * (attempt + 1)
            log(f"命中可重试错误，{wait}s 后重试 ({attempt + 1}/2)")
            time.sleep(wait)
            continue
        return False, f"publish 失败 (exit {proc.returncode})"
    return False, "publish 失败：重试耗尽"


def main() -> int:
    ap = argparse.ArgumentParser(description="把仓库里的 Skill 发布到 SkillHub")
    ap.add_argument("--config", default=str(ROOT / "publish.config.json"))
    ap.add_argument("--only", help="只发布指定 slug（可重复理解为一个）")
    ap.add_argument("--dry-run", action="store_true", help="全流程预检，不接触凭证、不提交发布")
    ap.add_argument("--version-source", choices=["frontmatter", "tag", "env"], default=None)
    ap.add_argument("--tag", default=os.environ.get("GITHUB_REF_NAME", ""))
    ap.add_argument("--base-ref", default=os.environ.get("PUBLISH_BASE_REF", ""),
                    help="上一版本引用（上一个 tag 或 PR base），用于版本递增校验")
    ap.add_argument("--changelog", default=None)
    ap.add_argument("--strict", action="store_true", help="preflight 警告升级为阻断")
    ap.add_argument("--host", default=None)
    args = ap.parse_args()

    cfg = load_config(Path(args.config))
    host = args.host or cfg.get("host") or os.environ.get("SKILLHUB_HOST") or DEFAULT_HOST
    version_source = args.version_source or cfg.get("version_source") or "frontmatter"
    skills = discover_skills(cfg)
    if args.only:
        skills = [s for s in skills if s["slug"] == args.only]
    if not skills:
        log("没有找到待发布的 Skill（检查 publish.config.json 或 skills/ 目录）")
        return 1

    staged_dirs: list[Path] = []
    results: list[dict] = []
    try:
        if not args.dry_run:
            token = os.environ.get("SKILLHUB_KEY", "").strip()
            if not token or not token.startswith("skh_"):
                raise RuntimeError(
                    "缺少环境变量 SKILLHUB_KEY（形如 skh_...）。"
                    "CI 里请配置为 GitHub Actions Secret，本地请从密码管理器读取后 export。"
                )
            cli = find_cli()
            run([cli, "--version"], check=True)
            login(cli, host, token)
        else:
            cli = ""  # dry-run 不需要凭证，但保留 CLI 用于本地 dry-run 预检
            try:
                cli = find_cli()
                run([cli, "--version"], check=False)
            except RuntimeError:
                log("未安装 skillhub CLI，跳过 CLI dry-run，仅执行本地 preflight + 打包校验")

        for item in skills:
            rel = item["path"]
            skill_dir = ROOT / rel
            slug = item["slug"]
            log(f"--- {slug} ({rel}) ---")
            entry: dict = {"slug": slug, "path": rel, "ok": False,
                           "version": "", "detail": "", "url": ""}
            results.append(entry)
            try:
                staged = stage(skill_dir, None, args.config, version_source,
                               args.tag or None, args.strict,
                               overrides=item["overrides"])
                staged_dirs.append(staged)
                meta, _, _, _ = fm.resolve_meta(skill_dir, item["overrides"],
                                                version_source, args.tag or None)
                version = str(meta.get("version", ""))
                entry["version"] = version
                entry["url"] = f"https://skillhub.cn/skills/{meta.get('slug')}"
                if not args.dry_run:
                    check_version_bump(rel, version, args.base_ref or None)
                changelog = make_changelog(args.changelog, version,
                                           args.base_ref or None, rel)
                if cli:
                    ok, detail = publish_one(cli, host, staged, changelog, args.dry_run)
                else:
                    ok, detail = True, "本地 preflight + 打包通过（CLI 未安装，未跑 CLI dry-run）"
                entry["ok"], entry["detail"] = ok, detail
                log(f"{slug}@{version}: {detail}")
            except (RuntimeError, fm.FrontmatterError, OSError) as exc:
                entry["detail"] = str(exc)
                log(f"{slug}: 失败 -> {exc}")
    finally:
        for d in staged_dirs:
            shutil.rmtree(d, ignore_errors=True)

    print("\n=== 发布汇总 ===")
    for r in results:
        mark = "OK  " if r["ok"] else "FAIL"
        print(f"  [{mark}] {r['slug']}@{r['version'] or '-'} :: {r['detail']}")
        if r["ok"] and not args.dry_run:
            print(f"          {r['url']}  (status=pending_review 表示已提交待审)")
    failed = [r for r in results if not r["ok"]]
    if failed:
        log(f"{len(failed)}/{len(results)} 个 Skill 未成功，退出码 1")
        return 1
    log(f"{len(results)}/{len(results)} 个 Skill 处理完成" + ("（dry-run）" if args.dry_run else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
