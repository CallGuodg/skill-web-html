"""发布策略的单一事实来源 (Single Source of Truth).

preflight.py 与 stage.py 必须共用这里的规则，确保「校验过的文件集合」
与「真正打包上传的文件集合」完全一致，避免出现「校验放行、包里却带上 .env」。

参考平台：skillhub.cn (API host: https://api.skillhub.cn)
"""
from __future__ import annotations

import re
from pathlib import Path

# ---------------------------------------------------------------------------
# 打包过滤：这些文件/目录绝不能进入发布包
# 依据：SkillHub 对 .github/、.gitignore、无扩展名 LICENSE、.claude-plugin/
#      等会返回 400；凭证类文件属于安全事故，一律排除。
# ---------------------------------------------------------------------------
EXCLUDE_DIRS = {
    ".git", ".github", ".gitlab", ".workbuddy", ".codebuddy",
    ".claude", ".claude-plugin", ".codex", ".agents",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    "node_modules", ".venv", "venv", "env",
    "dist", "build", "out", "output", "logs", "tmp", "temp",
    "coverage", ".idea", ".vscode", ".gradle", "target",
}

EXCLUDE_FILES = {
    # 平台明确拒收 / 无需发布
    ".gitignore", ".gitattributes", ".gitmodules", ".DS_Store",
    "LICENSE", "LICENCE", "NOTICE", "COPYING",
    "publish.json", "publish.config.json",
    ".npmrc", ".editorconfig", "Thumbs.db", "desktop.ini",
    # 凭证与私有配置
    "config.json", "credentials.json", "auth.json", "token.json",
    "secrets.json", ".env", "id_rsa", "id_ed25519",
}

EXCLUDE_PREFIXES = (".env",)  # .env.local / .env.production ...
EXCLUDE_SUFFIXES = (
    ".pyc", ".pyo", ".log", ".zip", ".tar", ".gz", ".7z",
    ".exe", ".dll", ".so", ".dylib", ".class", ".jar",
    ".woff", ".woff2", ".ttf", ".mp4", ".mp3", ".mov",
    ".bak", ".tmp", ".swp",
)

# 允许进包的文本/资源后缀。二进制大文件会被平台扫描门控拦下。
ALLOWED_SUFFIXES = {
    ".md", ".markdown", ".txt", ".rst",
    ".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx",
    ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf",
    ".sh", ".bash", ".zsh", ".ps1", ".bat", ".cmd",
    ".css", ".html", ".htm", ".svg", ".csv", ".sql",
    ".go", ".java", ".rb", ".rs", ".c", ".h", ".cpp", ".r", ".php",
    ".png", ".jpg", ".jpeg", ".webp", ".gif",
}

MAX_FILE_BYTES = 1024 * 1024          # 单文件 1 MiB
MAX_TOTAL_BYTES = 8 * 1024 * 1024    # 整包 8 MiB（SkillHub 常见上限）

SKILL_MD = "SKILL.md"

# ---------------------------------------------------------------------------
# SkillHub 发布必需的 frontmatter
# ---------------------------------------------------------------------------
REQUIRED_META = ("slug", "version", "displayName")
# 官方 Agent 发布规范列的「建议检查但不阻断」项；iconUrl 不在此列，仅在渲染时保留
RECOMMENDED_META = ("name", "description", "summary", "tags", "license", "homepage")

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*[a-z0-9]$")
SEMVER_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)

# 密钥 / 隐私特征：命中即阻断发布
SECRET_PATTERNS = [
    (re.compile(r"skh_[A-Za-z0-9]{16,}"), "SkillHub API Key (skh_)"),
    (re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"), "GitHub Token (gh*_)"),
    (re.compile(r"AKIA[0-9A-Z]{16}"), "AWS Access Key ID"),
    (re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"), "私钥内容"),
    (re.compile(r"(?i)bearer\s+[A-Za-z0-9._-]{24,}"), "Bearer Token"),
    (re.compile(
        r"(?i)(?:api[_-]?key|api[_-]?secret|access[_-]?token|password)\s*[:=]\s*"
        r"[\"'][A-Za-z0-9_\-]{20,}[\"']"
    ), "疑似硬编码凭证"),
]

LOCAL_PATH_PATTERNS = [
    (re.compile(r"/Users/[A-Za-z0-9._-]+/"), "macOS 本地绝对路径"),
    (re.compile(r"/home/[A-Za-z0-9._-]+/"), "Linux 本地绝对路径"),
    (re.compile(r"[A-Za-z]:\\Users\\", re.I), "Windows 本地绝对路径"),
    (re.compile(r"[A-Za-z]:\\(?:FileHub_Share|workbuddy|CodeBuddy)\\", re.I), "本机工作区绝对路径"),
]


def is_excluded_dir(name: str) -> bool:
    return name in EXCLUDE_DIRS or name.startswith(".")


def is_excluded_file(name: str) -> bool:
    lower = name.lower()
    if name in EXCLUDE_FILES:
        return True
    if any(name.startswith(p) or lower.startswith(p) for p in EXCLUDE_PREFIXES):
        return True
    if any(name.endswith(s) for s in EXCLUDE_SUFFIXES):
        return True
    return False


def is_allowed_suffix(name: str) -> bool:
    return Path(name).suffix.lower() in ALLOWED_SUFFIXES


def collect_files(skill_dir: Path) -> list[Path]:
    """返回按 policy 过滤后、真正会进入发布包的相对路径列表（已排序）。

    注意：SKILL.md 与无后缀的辅助文件不走后缀白名单，但仍走排除名单。
    """
    result: list[Path] = []
    for path in sorted(skill_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(skill_dir)
        parts = rel.parts
        # 目录段过滤（排除最后一段文件名）
        if any(is_excluded_dir(p) for p in parts[:-1]):
            continue
        if is_excluded_file(path.name):
            continue
        # 隐藏文件（如 .prettierrc）一律不进包
        if path.name.startswith("."):
            continue
        if not is_allowed_suffix(path.name):
            continue
        result.append(rel)
    return result
