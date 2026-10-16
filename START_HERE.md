# GitHub Pages 首次发布

## 当前状态

本压缩包没有替你创建远程仓库、开通 Pages 或启动定时任务。附带脚本用于在你自己的电脑上执行这些操作。采集器、网页和发布逻辑已做离线检查；当前环境的外部域名解析失败，因此没有完成真实网络端到端采集和 GitHub 发布验证。

默认目标为新建 `HaoranWy/paper-radar`。**仓库、研究关键词、论文元数据和网站都将公开。** 不要向项目添加私人手稿、个人笔记、密码或 API Key。脚本有上传文件白名单，但不能判断你自行写入配置文件的内容是否敏感。它不会修改你已有的研究仓库，不会覆盖同名旧仓库，不会强制推送。

## 1. 准备工具（只在首次需要时安装）

需要 Python 3.11+、Git、GitHub CLI（命令名 `gh`）。本地不需要安装采集器的 Python 依赖；GitHub Actions 会安装。

Windows PowerShell 可以先检查：

```powershell
python --version
git --version
gh --version
```

缺少 Git 或 GitHub CLI 时可以使用官方安装程序，或通过 Windows 的 WinGet：

```powershell
winget install --id Git.Git --exact
winget install --id GitHub.cli --exact
```

安装后重新打开 PowerShell，让新 PATH 生效。Python 已有可用环境则不必重装。

## 2. 解压并登录

解压整个压缩包，进入包含 `deploy_github.py` 的 `paper-radar` 文件夹；不要只上传 ZIP 文件或 `preview.html`。

```powershell
cd "你解压后的完整路径\paper-radar"
gh auth login --hostname github.com --git-protocol https --web --scopes repo,workflow
```

按照终端提示，在浏览器登录并授权 `HaoranWy`。不需要把密码、Token 或登录验证码发给任何人。`gh` 的本地登录独立于聊天里的 GitHub 连接。

如本机已经登录另一个账号，用 `gh auth switch --hostname github.com --user HaoranWy` 切换；如需要补充权限，用 `gh auth refresh --hostname github.com --scopes repo,workflow`。授权范围用于创建仓库、上传工作流及配置 Pages。

## 3. 执行首次发布

```powershell
python deploy_github.py --check
python deploy_github.py --owner HaoranWy --repo paper-radar --public
```

Windows 也可用 `py -3` 代替 `python`；Linux/macOS 根据环境使用 `python3`。

最后一条命令是你的公开发布授权。脚本将依次检查账号、新建公开仓库、初始化并上传项目、把默认分支设为 main、配置 Pages 使用 Actions，然后提交首次运行请求。它会显示工作流地址，不会把“已提交运行请求”冒充“网站已成功上线”。

发布成功后，默认项目网址预计为：

```text
https://haoranwy.github.io/paper-radar/
```

**这只是预期地址，必须以成功的 `deploy` 作业输出为准，不能在部署前把它当成已上线网站。**

## 4. 检查是否真的完成

进入新仓库 → **Actions → Daily Paper Radar**。看三个作业：

- `build`：测试、采集、构建网页、保存档案、打包网页。
- `deploy`：实际发布网页，成功后给出站点地址。
- `source-health`：若采集异常或档案保存失败，会在网页发布后报告错误；此时网页可能仍已发布，但来源需要排查。

打开网页后检查“来源状态”“最近采集运行结束”和论文条目。某来源可能尚无公开会集、只有部分结果或受到访问限制；不能仅凭部署成功断言已经覆盖所有来源。

默认每天北京时间 **08:17**，工作流采用 UTC 表达式 `17 0 * * *`。定时调度不是准点保证；公开仓库长期无活动可能停用计划任务。项目会保存来源状态，但仍需关注失败通知和页面超过 36 小时未更新的提示。

## 常见问题

**同名仓库已存在**：脚本会停止，不覆盖任何文件。更改 `--repo paper-radar-daily` 可新建另一个仓库，站点路径也随名称变化。若是本脚本的上次部署中断，保留 `.deploy-state.json` 和同一解压目录后重跑，可恢复已记录的步骤。

**没有找到 gh**：安装 GitHub CLI 并重开终端。`gh --version` 成功后再继续。

**上传 workflow 权限不足**：运行上面的 `gh auth refresh`，完成浏览器授权后在原目录重跑。不要使用 `git push --force`。

**Pages 配置错误或组织策略阻止**：在仓库 **Settings → Pages → Build and deployment → Source** 选择 **GitHub Actions**。检查 **Settings → Actions → General** 是否允许工作流运行。脚本不会放宽组织安全策略。

**首次 dispatch 返回 404**：确认文件 `.github/workflows/daily.yml` 在 `main` 根目录下，然后从 **Actions → Daily Paper Radar → Run workflow** 手动运行。工作流文件不能放在额外的 `paper-radar/` 子目录。

**只有部分来源失败**：先查看 `build` 日志和网页来源状态，其他来源与旧数据仍可显示。初版并未完成所有源站在线验证，具体适配器可能需要根据实际日志调整。

**需要邮箱或付费 API Key 吗**：初版不需要模型 API Key。`CROSSREF_MAILTO` 是可选的真实联系邮箱，可在仓库的 Actions Secrets 配置。公开诊断信息会对该字段及常见凭据查询参数做脱敏。不要在公开配置或网页里写入密钥。

**修改研究方向**：编辑 `config.yaml` 的 `topics` 并提交到 `main`，或手动触发工作流。默认沿用智能体、自进化、不确定性量化、可靠停止与推理预算，并包含 AI for Science 等扩展主题。

## 官方参考

- GitHub CLI 安装：https://github.com/cli/cli#installation
- Git 安装：https://git-scm.com/install/windows
- gh 浏览器登录：https://cli.github.com/manual/gh_auth_login
- 创建仓库：https://cli.github.com/manual/gh_repo_create
- Pages 配置 API：https://docs.github.com/en/rest/pages/pages
- 自定义 Pages 工作流：https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages
- 调度行为：https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule
