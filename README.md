# skill-web-html

把网页文章保存为**单文件、可离线阅读的 HTML** 的 Agent Skill。
仓库自带 GitHub → [SkillHub](https://skillhub.cn) 自动发布流水线：打 `v*` tag 即可提交上架。

- GitHub：<https://github.com/CallGuodg/skill-web-html>（public，账号 `CallGuodg`）
- 发布目标：<https://skillhub.cn/skills/skill-web-html>
- 分支模型：Trunk-Based，`main` 始终可发布；`push main` **不**自动发版，只由 tag 触发

> **当前状态：骨架。** `SKILL.md` 元数据、CI 校验、发布链路都已就绪；
> 抓取与资源内联的具体实现待补。补齐前请只跑 `--dry-run`，不要打正式 tag 发布。

---

## 1. 快速开始

```bash
# 本地全流程预检（不需要任何 Token）
python scripts/publish.py --dry-run

# 查看究竟哪些文件会进发布包
python scripts/stage.py skills/skill-web-html --out .stage/skill-web-html

# 单个 skill 的机读校验报告
python scripts/preflight.py skills/skill-web-html --json
```

`--dry-run` 会依次跑：frontmatter 校验 → 密钥/隐私扫描 → 生成干净发布副本 →
官方 CLI 的 `skillhub publish --dry-run`，全程不接触凭证、不发 HTTP 之外的发布请求。

---

## 2. 目录结构

```
.
├── skills/skill-web-html/
│   ├── SKILL.md                          # 技能定义（双平台兼容 frontmatter）
│   └── references/implementation-notes.md
├── .github/workflows/
│   ├── skill-pr-check.yml                # PR：只预检，零凭证
│   └── skill-publish.yml                 # tag / 手动：预检 + 发布
├── scripts/                              # 校验、打包、发布编排（5 个脚本）
├── publish.config.json                   # host / skill 列表 / 元数据覆盖
├── .gitattributes                        # 强制 LF，防 CRLF 破坏 CI shell 块
├── .gitignore
└── docs/
    ├── SETUP.md                          # 一次性接入清单
    └── TROUBLESHOOTING.md                # 错误码对照与 Git 恢复步骤
```

**核心设计**：`policy.py` 是校验与打包的单一事实来源，`preflight.py` 与 `stage.py`
共用同一套排除规则，不会出现「校验放行、包里却带上凭证文件」的错位。
`stage.py` 只生成临时干净副本，**绝不修改源 Skill 目录**。

---

## 3. 发布一个版本

1. 补齐 `skills/skill-web-html/` 里的实现（或把已有的网页存档脚本搬进来）。
2. 递增 `SKILL.md` 的 `version`（SemVer），**`slug` 保持不变**。
3. 从最新 `main` 开 `feat/*` 分支 → PR 预检通过 → 合并 `main`。
4. 打 tag 发版：

```bash
git switch main && git pull --ff-only origin main
git tag -a v0.1.0 -m "chore(release): skill-web-html 0.1.0"
git push origin v0.1.0
```

5. Actions 日志出现 `Published: skillId=... status=pending_review` 即已提交待审
   （这是正常状态，不是失败）。去 SkillHub「我的 Skill」看三线安全审核结果。

发版前 CI 会自动用上一个 tag 校验版本确实递增，并从两次 tag 之间的
Conventional Commits 生成 `--changelog`。

---

## 4. 一次性配置（涉及身份，只能你本人做）

1. 在 skillhub.cn 注册并完成**实名认证**（未实名 CLI 返回 403）。
2. 「个人中心 → API keys」创建 Token（`skh_...`，只显示一次，存进密码管理器）。
3. 本仓库 `Settings → Secrets and variables → Actions` 添加 Secret `SKILLHUB_KEY`；
   建议再加 Variable `SKILLHUB_PUBLISHER` 填你的 handle。

也可以装了 `gh` 后用不回显的方式写入（自己在本机粘贴，不要经过聊天或文件）：

```bash
read -rs SKILLHUB_KEY && printf %s "$SKILLHUB_KEY" \
  | gh secret set SKILLHUB_KEY --repo CallGuodg/skill-web-html && unset SKILLHUB_KEY
```

详细步骤（Environment 人工审批、分支保护、tag 保护）见 [docs/SETUP.md](docs/SETUP.md)。
**首次建议先手动跑 `dry_run=true` 的 dispatch，再打真 tag。**

---

## 5. 平台契约速查

`SKILL.md` frontmatter 必填（缺一即被平台阻断）：

| 字段 | 要求 |
| --- | --- |
| `slug` | kebab-case，2–128 字符，**全网唯一，发布后不可改** |
| `version` | 合法 SemVer，如 `0.1.0` |
| `displayName` | 非空展示名 |

保留 `name` + `description`，同一份 SKILL.md 在本机 WorkBuddy / Claude Code 里也能触发。
已查过 SkillHub 搜索，`skill-web-html` 暂无同名占用；但**最终以正式发布是否 409 为准**，
若冲突请改成带 handle 的 slug 并同步重命名目录。

会被自动排除、不进发布包（平台对这些返回 400）：`.github/`、`.gitignore`、
无扩展名 `LICENSE`、`.env*`、`credentials.json`、`__pycache__`、二进制产物。

---

## 6. 分支规范

```bash
git fetch origin
git switch -c feat/web-fetch origin/main        # 从最新 main 开分支
git commit -m "feat(web-html): 增加正文提取"     # Conventional Commits
git fetch origin && git rebase -i origin/main   # 合并前整理成原子提交
git push --force-with-lease origin feat/web-fetch
```

- **不强制推送 `main`**；回退用 `git revert` 生成新提交。
- PR 必须过 `Skill Preflight` 检查（在分支保护里设为 required）。
- 保护 `v*` tag，避免被改指或误删。

---

## 7. 安全边界

- Token 只从 `SKILLHUB_KEY` 环境变量读取，不写文件、不回显日志；PR 与预检 job 拿不到 Secret。
- preflight 扫描**将要进包的文件**，命中凭证特征直接阻断。
- `SKILLHUB_PUBLISHER` 存在时用 `skillhub auth whoami` 核对身份，防止发错账号。
- 本仓库不存储任何 API Key。

排障见 [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)。
