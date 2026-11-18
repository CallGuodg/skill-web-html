# 一次性接入清单

按顺序做完 A–C，之后每次发版只需要「改 `version` → 打 tag → push」。

仓库：<https://github.com/CallGuodg/skill-web-html>（public，默认分支 `main`）。
本地目录：`D:/FileHub_Share/AI Workspace/workbuddy/skill-web-html`。

---

## A. SkillHub 侧（只能本人操作）

1. 打开 <https://skillhub.cn>，右上角「登录 / 注册」，手机号 + 短信验证码。首次登录自动注册。
2. 头像 → 个人中心 → **实名认证**（腾讯云人脸核身）。
   未实名不能创建 API Key，也不能发布，CLI 会返回 `403 请先完成实名认证`。
3. 个人中心 → **API keys**（<https://skillhub.cn/dashboard/keys>）→ 创建 API key，
   起个可识别的名字，例如 `github-actions-skill-web-html`。
4. 弹窗里的完整 Token（`skh_...`）**只显示一次**，立刻复制进密码管理器。
   不要发到聊天、不要截图、不要写进仓库或 workflow YAML。

> 团队发布：由超级管理员完成企业/管理员认证，并从「团队密钥页」生成团队 API 密钥，
> 不要复用个人 `SKILLHUB_KEY`。

---

## B. GitHub 侧

### B1. 仓库已就绪

远端仓库和本地目录都已初始化完成，`origin` 已指向
`https://github.com/CallGuodg/skill-web-html.git`。换机器时直接：

```bash
git clone https://github.com/CallGuodg/skill-web-html.git
cd skill-web-html
```

若要把流水线搬到别的仓库，只拷这四样，不要覆盖对方已有的 workflow：

```
.github/workflows/skill-pr-check.yml
.github/workflows/skill-publish.yml
scripts/
publish.config.json
```

然后按 `publish.config.json` 的 `skills[].path` 指向目标 skill 目录。
若 `skills` 留空数组，脚本会自动扫描 `skills/` 下所有含 `SKILL.md` 的目录。

### B2. 添加 Secret 与 Variable

`Settings → Secrets and variables → Actions`：

| 类型 | 名称 | 值 | 必需 |
| --- | --- | --- | --- |
| Secret | `SKILLHUB_KEY` | A 步拿到的 `skh_...` | ✅ |
| Variable | `SKILLHUB_PUBLISHER` | 你的 SkillHub handle | 建议 |

`SKILLHUB_PUBLISHER` 非敏感，放 Variable 不要放 Secret。配了之后 CI 会用
`skillhub auth whoami` 比对，不一致就中止，防止用错账号发布。

用 `gh` 写入（不回显、不进 shell history）：

```bash
read -rs SKILLHUB_KEY && printf %s "$SKILLHUB_KEY" \
  | gh secret set SKILLHUB_KEY --repo CallGuodg/skill-web-html && unset SKILLHUB_KEY
gh variable set SKILLHUB_PUBLISHER --repo CallGuodg/skill-web-html --body "<你的 handle>"
```

### B3. 给发布加一道人工审批（可选但建议）

`Settings → Environments → New environment`，名字填 **`skillhub-production`**
（workflow 里已写死这个名字），勾选 `Required reviewers` 把自己加进去。
这样每次 tag 触发时，预检通过后还会停一下等你点批准才真正提交发布。

不创建这个 environment 也能跑——GitHub 会自动创建一个无保护规则的空 environment。

### B4. 分支保护

`Settings → Branches → Add rule`，Branch name pattern = `main`：

- ✅ Require a pull request before merging
- ✅ Require status checks to pass → 搜索并勾选 `阻断式预检 + CLI dry-run`
  （PR 跑过一次后才会出现在候选列表里）
- ✅ Do not allow bypassing the above settings
- ❌ 不要开 force push

另在 `Settings → Rules → Tags`（或 tag protection）里保护 `v*`，防止 tag 被改指。

---

## C. 首次验证（先别打真 tag）

> 当前 skill 仍是**骨架**，抓取与内联实现未补齐。建议先只验证链路，不要正式发布。

1. 本地跑 `python scripts/publish.py --dry-run`，确认全绿。
2. `Actions → Publish Skills to SkillHub → Run workflow`，勾上 **`dry_run = true`**。
   预期：`publish` job 被跳过，`preflight` 全绿，日志出现 `dry-run passed`。
3. 实现补齐、确认 slug 无冲突后，再真实发版：

```bash
# 确认 SKILL.md 的 version 已递增（首次 0.1.0）
git add skills/
git commit -m "feat(skill-web-html): 补齐网页抓取与资源内联"
git push origin main

git tag -a v0.1.0 -m "chore(release): skill-web-html 0.1.0"
git push origin v0.1.0
```

4. Actions 日志出现 `✓ Published: skillId=... status=pending_review` 即链路打通。
   到 <https://skillhub.cn/dashboard>「我的 Skill」看三线安全审核（内容合规过滤、
   科恩实验室漏洞扫描、云鼎实验室 AI 模型安全评估）结果。

---

## D. 后续每次迭代

```bash
# 改完 skill 内容
# 必做：递增 SKILL.md 里的 version（slug 保持不变）
git commit -am "fix(skill-web-html): 修正长文分册的编码识别"
git push origin main
git tag -a v0.1.1 -m "chore(release): skill-web-html 0.1.1"
git push origin v0.1.1
```

`--base-ref` 会自动取上一个 tag，用来：
1. 校验 `version` 确实递增了（没递增直接阻断）；
2. 从两个 tag 之间的 Conventional Commits 自动生成 `--changelog`。

---

## E. Token 轮换

Token 泄露或人员变动时：到 SkillHub「API keys」撤销旧 Key、新建一把，
再到 GitHub 更新 `SKILLHUB_KEY` Secret。仓库里没有任何地方需要改。
轮换后建议跑一次 `dry_run=false` 的手动 dispatch 验证身份核对仍通过。

---

## F. 发布前最容易踩的两个坑

- **slug 被占用 → 409**。当前用 `skill-web-html`，已查搜索无同名，但以正式发布结果为准。
  若冲突改成带 handle 的形式（例如 `callguodg-web-html`），并**同步重命名目录**
  与 `publish.config.json` 里的 `path`/`slug`，否则 preflight 会提示不一致。
- **license 决定能否商用**。当前是 `MIT`；若不想公开授权，改成 `UNLICENSED`。
  一旦公开发布就是不可逆的授权，发布前想清楚。
