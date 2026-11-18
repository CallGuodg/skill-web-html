"""零依赖的 SKILL.md frontmatter 解析 / 合并 / 渲染。

为什么不直接用 PyYAML：CI runner 与本机都不保证装了第三方包，而发布校验
最关键的恰恰是「平台 YAML 解析器会拒绝的写法」，用一个能显式报告这类
写法的轻量解析器更可控。若检测到 PyYAML 可用，parse 阶段会额外做一次
交叉验证。
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

import policy

BLOCK_MARKERS = (">", ">-", ">+", "|", "|-", "|+")
KEY_RE = re.compile(r"^([A-Za-z][A-Za-z0-9_-]*)\s*:\s*(.*)$")


class FrontmatterError(Exception):
    pass


def split_frontmatter(text: str) -> tuple[list[str], str]:
    """返回 (frontmatter 行列表, 正文)。"""
    lines = text.replace("\r\n", "\n").split("\n")
    if not lines or lines[0].strip() != "---":
        raise FrontmatterError("SKILL.md 缺少开头的 --- frontmatter 分隔符")
    for idx in range(1, len(lines)):
        if lines[idx].strip() in ("---", "..."):
            return lines[1:idx], "\n".join(lines[idx + 1:]).lstrip("\n")
    raise FrontmatterError("SKILL.md 的 frontmatter 没有闭合的 ---")


def _decode_scalar(raw: str) -> object:
    raw = raw.strip()
    if not raw:
        return ""
    if raw.startswith("[") and raw.endswith("]"):
        inner = raw[1:-1].strip()
        if not inner:
            return []
        return [x.strip().strip("\"'") for x in inner.split(",") if x.strip()]
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in ("\"", "'"):
        return raw[1:-1]
    return raw


def parse(text: str) -> tuple[dict, str, list[dict]]:
    """解析 frontmatter。

    返回 (meta, body, issues)，issue 形如 {"level": "error"|"warn", "code": ..., "msg": ...}
    行号按平台口径：去掉首个 --- 之后，name 记作第 1 行。
    """
    fm_lines, body = split_frontmatter(text)
    meta: dict = {}
    issues: list[dict] = []
    i = 0
    while i < len(fm_lines):
        raw_line = fm_lines[i]
        lineno = i + 1  # 平台口径
        stripped = raw_line.strip()

        if not stripped:
            i += 1
            continue
        if stripped.startswith("#"):
            issues.append({"level": "warn", "code": "frontmatter-comment",
                           "msg": f"第 {lineno} 行是注释，部分平台会忽略整行"})
            i += 1
            continue

        m = KEY_RE.match(raw_line)
        if not m:
            if raw_line.startswith((" ", "\t")) and meta:
                # 孤立的缩进行，容错跳过
                i += 1
                continue
            issues.append({"level": "error", "code": "frontmatter-no-colon",
                           "msg": f"第 {lineno} 行不是 key: value 结构: {stripped[:60]}"})
            i += 1
            continue

        key, raw_value = m.group(1), m.group(2)
        if key in meta:
            issues.append({"level": "warn", "code": "frontmatter-dup-key",
                           "msg": f"第 {lineno} 行重复键 {key}，后者会覆盖前者"})

        if raw_value.strip() in BLOCK_MARKERS:
            marker = raw_value.strip()
            collected: list[str] = []
            j = i + 1
            while j < len(fm_lines) and (fm_lines[j].startswith((" ", "\t")) or not fm_lines[j].strip()):
                collected.append(fm_lines[j].strip())
                j += 1
            value = "\n".join(collected) if marker.startswith("|") else " ".join(x for x in collected if x)
            meta[key] = value.strip()
            i = j
            continue

        # plain scalar 的坑：未加引号时，值里的 ": " 会让 YAML 解析器报
        # "mapping values are not allowed in this context"；值里的 " #" 会被当注释截断。
        if raw_value and raw_value[0] not in ("\"", "'", "[", "{", "&", "*"):
            v = raw_value.strip()
            if ": " in v or v.endswith(":"):
                issues.append({"level": "error", "code": "frontmatter-colon",
                               "msg": f"第 {lineno} 行 {key} 的 plain scalar 含半角冒号+空格或以冒号结尾，"
                                      "平台 YAML 解析会失败；请加引号、改用全角冒号或 >- 块标量"})
            if " #" in v:
                issues.append({"level": "warn", "code": "frontmatter-hash",
                               "msg": f"第 {lineno} 行 {key} 含 ' #'，YAML 会把它当注释截断"})

        meta[key] = _decode_scalar(raw_value)
        i += 1

    return meta, body, issues


def _encode_scalar(value: object) -> str:
    """渲染单个字段，保证写回后一定能被 YAML 解析。"""
    if isinstance(value, list):
        # 每个元素都加引号，避免含空格的标签被 YAML 解析器截断
        return "[" + ", ".join(json.dumps(str(x), ensure_ascii=False) for x in value) + "]"
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return '""'
    s = str(value)
    if "\n" in s or len(s) > 120:
        # 块标量：内部随便写冒号都安全
        block = "\n".join("  " + line for line in s.split("\n") if line.strip())
        return ">-\n" + block
    return json.dumps(s, ensure_ascii=False)


def render(meta: dict, body: str) -> str:
    """把合并后的元数据渲染成完整的 SKILL.md。"""
    order = ["name", "slug", "displayName", "version", "description",
             "summary", "tags", "license", "homepage", "iconUrl", "author", "category"]
    keys = [k for k in order if k in meta]
    keys += sorted(k for k in meta if k not in keys and not k.startswith("_"))
    lines = ["---"]
    for key in keys:
        lines.append(f"{key}: {_encode_scalar(meta[key])}")
    lines.append("---")
    lines.append("")
    return "\n".join(lines) + body.rstrip() + "\n"


def _version_from_tag(tag: str | None) -> str | None:
    if not tag:
        return None
    m = re.fullmatch(r"v?(\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?)", tag.strip())
    return m.group(1) if m else None


def resolve_meta(skill_dir: Path, overrides: dict | None = None,
                 version_source: str = "frontmatter",
                 tag: str | None = None) -> tuple[dict, str, list[dict], list[dict]]:
    """计算「最终发布副本」的元数据。

    返回 (meta, body, errors, warnings)。preflight 与 stage 共用，保证一致。
    version_source:
      frontmatter —— 直接用 SKILL.md 里的 version（发布前手动 bump，默认）
      tag         —— 从 vX.Y.Z tag 提取，tag 即唯一版本源
      env         —— 从 SKILL_VERSION 环境变量读取
    """
    skill_md = skill_dir / policy.SKILL_MD
    if not skill_md.is_file():
        raise FrontmatterError(f"未找到 {skill_md}")
    text = skill_md.read_text(encoding="utf-8")
    meta, body, issues = parse(text)
    errors = [x for x in issues if x["level"] == "error"]
    warnings = [x for x in issues if x["level"] == "warn"]

    if overrides:
        meta.update({k: v for k, v in overrides.items() if not k.startswith("_")})

    # slug / displayName 的兜底：让只写了 name+description 的 WorkBuddy 技能也能发布
    if not meta.get("slug"):
        meta["slug"] = skill_dir.name
    if not meta.get("displayName"):
        meta["displayName"] = str(meta.get("name") or skill_dir.name)

    if version_source == "tag":
        v = _version_from_tag(tag)
        if not v:
            errors.append({"level": "error", "code": "version-tag-invalid",
                           "msg": f"version_source=tag 但 tag {tag!r} 不是 vX.Y.Z 格式"})
        else:
            meta["version"] = v
    elif version_source == "env":
        v = os.environ.get("SKILL_VERSION", "").strip()
        if v:
            meta["version"] = v

    # 平台硬性校验
    slug = str(meta.get("slug", ""))
    if not policy.SLUG_RE.fullmatch(slug) or not (2 <= len(slug) <= 128):
        errors.append({"level": "error", "code": "slug-invalid",
                       "msg": f"slug={slug!r} 不合法：需 kebab-case（小写字母/数字/连字符），长度 2-128"})
    version = str(meta.get("version", ""))
    if not policy.SEMVER_RE.fullmatch(version):
        errors.append({"level": "error", "code": "version-invalid",
                       "msg": f"version={version!r} 不是合法 SemVer（例如 1.0.0）"})
    if not str(meta.get("displayName", "")).strip():
        errors.append({"level": "error", "code": "displayName-missing", "msg": "displayName 缺失"})
    for key in policy.RECOMMENDED_META:
        if not meta.get(key):
            warnings.append({"level": "warn", "code": f"recommended-{key}",
                             "msg": f"建议补充 {key}（不影响发布，但影响商店展示/触发）"})
    if slug != skill_dir.name:
        warnings.append({"level": "warn", "code": "slug-dir-mismatch",
                         "msg": f"slug={slug!r} 与目录名 {skill_dir.name!r} 不一致，容易发错对象"})

    return meta, body, errors, warnings


def cross_check_with_yaml(skill_dir: Path) -> list[dict]:
    """有 PyYAML 时做一次交叉验证；没装则跳过（不阻断）。"""
    try:
        import yaml  # type: ignore
    except ImportError:
        return []
    try:
        fm_lines, _ = split_frontmatter((skill_dir / policy.SKILL_MD).read_text(encoding="utf-8"))
        yaml.safe_load("\n".join(fm_lines))
        return []
    except Exception as exc:  # noqa: BLE001
        return [{"level": "error", "code": "yaml-parse-failed",
                 "msg": f"PyYAML 解析 frontmatter 失败：{exc}"}]
