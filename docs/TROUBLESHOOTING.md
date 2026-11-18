# 排障与恢复

> 判据：**`preflight` 通过 ≠ 一定能发布**。dry-run 只做本地 metadata 与打包检查，
> 不发 HTTP，因此不能确认登录态、发布权限、slug 是否全局可用或审核结果。
> 一切以服务端返回为准。

---

## 1. preflight 阻断码（本地/CI 同一套）

| 码 | 含义 | 处理 |
| --- | --- | --- |
| `frontmatter-unreadable` | `SKILL.md` 缺 `---` 或没闭合 | 补齐 YAML frontmatter 分隔符 |
| `frontmatter-no-colon` | 某行不是 `key: value` | 检查缩进与拼写，或用 `>` 块标量 |
| `frontmatter-colon` | plain scalar 里出现半角 `: ` 或以 `:` 结尾 | 平台会报 `mapping values are not allowed`；改成全角冒号 / 加引号 / `>-` 块标量 |
| `frontmatter-dup-key` | 重复键 | 删掉一个（警告，`--strict` 下阻断） |
| `slug-invalid` | 非 kebab-case 或长度不合规 | 只用小写字母、数字、连字符，2–128 字符 |
| `version-invalid` | 不是 SemVer | 改成 `1.0.0` 形式 |
| `displayName-missing` | 缺展示名 | 在 frontmatter 或 `publish.config.json` 的 overrides 里补 |
| `secret` | 包内命中 `skh_` / `ghp_` / 私钥等 | **立刻移除并轮换该凭证**；公开平台上传后无法删除 |
| `local-path` | 含 `/Users/...`、`C:\Users\...` | 改成相对路径（警告） |
| `referenced-but-excluded` | SKILL.md 引用了不会进包的文件 | 把文件移出排除名单覆盖的路径，或去掉引用（装完会跑不起来） |
| `file-size` / `total-size` | 单文件 >1 MB / 整包 >8 MB | 精简资源，大文件改成外链引用 |
| `empty-package` | 过滤后包为空 | 检查是不是整目录都被排除规则命中 |
| `version-tag-invalid` | `version_source=tag` 但 tag 不是 `vX.Y.Z` | 用 `v1.2.3` 格式打 tag，或改用 `frontmatter` |
| `config-invalid` | `publish.config.json` 不是合法 JSON | 修 JSON（注意尾逗号） |

---

## 2. SkillHub 服务端与 CLI

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| `403 请先完成实名认证` | 账号未实名 / 未认证团队 | 本人到个人中心做人脸核身；团队需超管完成企业认证 |
| `401` | Token 无效、过期、或用错 CLI | 确认用官方 CLI；Windows 的 `.CMD` 版在 Python subprocess 下可正常跑 dry-run，但 Git Bash 里可能不在 PATH；npm 版在 Windows 常见 401。重新 `skillhub login --key ... --host https://api.skillhub.cn` |
| `409 slug 已被占用` | slug 全网唯一性冲突 | 换成带 handle 的唯一 slug；**已发布过的 slug 不要改** |
| `400` | 包里有平台拒收文件 | `.github/`、`.gitignore`、无扩展名 `LICENSE`、`.claude-plugin/`、二进制文件。本流水线已自动排除，若手工发布请删掉 |
| `502 / 503 / 504`、超时 | 网关抖动 | `publish.py` 内置 2 次退避重试（5s / 10s）；仍失败就手动 dispatch 重跑，脚本幂等 |
| `command not found: skillhub` | 非交互 shell 不加载 `~/.zshrc` | 显式 `export PATH="$HOME/.local/bin:$PATH"`；workflow 里已写入 `$GITHUB_PATH` |
| `skillhub auth whoami` 身份不符 | Secret 配到了别的账号 | 核对 `SKILLHUB_PUBLISHER` 与 Token 所属 handle |
| 发布成功但下载到旧版 | 卡在安全扫描门控，公共 API 只显示已公开版本 | **不要据此推断审核失败**；升一个补丁版本（如 `1.0.1`）重传即可 |
| `status=pending_review` | 已提交待三线安全审核 | 正常状态，不是失败。去「我的 Skill」看结果 |
| `iconUrl` 缺失 | CLI 不自动上传头像 | 发布后用默认占位图，或在 frontmatter 里填外链图标 |

---

## 3. GitHub Actions

| 现象 | 处理 |
| --- | --- |
| `缺少 Secret SKILLHUB_KEY` | 到 `Settings → Secrets and variables → Actions` 添加；fork 的 PR 默认拿不到 Secret（本流水线 PR job 也不需要） |
| `publish` job 一直停在 Waiting | `environment: skillhub-production` 开了 Required reviewers，去点批准；不需要就删掉该 environment 规则 |
| push 到 main 没有触发发布 | 设计如此。请打 `v*` tag 或手动 Run workflow |
| 手动 dispatch 选了 `version_source=tag` 报错 | dispatch 的 `github.ref_name` 是分支名不是 tag，用 `frontmatter`；`tag` 只用于 tag push |
| 两个 job 都跑但没发布 | 检查是不是勾了 `dry_run=true`；`publish` job 的 `if` 会跳过 |
| 重复发布同一版本 | `concurrency` 已按 ref 加锁；若已发生，去平台确认状态，不要删 tag 重试 |

---

## 4. Git 侧恢复（安全版本优先）

```bash
# 推错自己的功能分支：带 lease 的强推，不会覆盖别人已推的提交
git push --force-with-lease origin feat/my-skill

# 误提交了凭证：先从工作区清除并轮换凭证，再用 revert 而不是改写 main 历史
git revert <bad-commit>
git push origin main

# rebase / reset 丢了提交：用 reflog 找回
git reflog
git switch -c rescue/<name> <reflog-中的 sha>

# 打错 tag：删除本地 tag，并显式删远端 tag（这是少数必须强推的操作）
git tag -d v0.1.0
git push origin --delete v0.1.0
# 若 tag 已触发发布，先到 SkillHub「我的 Skill」确认状态，再决定是否重发新版本

# 把分支对齐到最新目标分支再提 PR
git fetch origin
git rebase origin/main
```

**绝不**对 `main` 用 `git push --force`；**绝不**把 Token 写进 commit message、YAML 或日志。
