# 论文雷达 · Paper Radar

**GitHub Pages 用户先读 [`START_HERE.md`](START_HERE.md)**：附账号核对、公开发布授权与首次部署脚本。当前压缩包尚未部署远程仓库。

每日运行 Python 采集器，生成一个无需前端框架的中文论文网页。默认面向智能体、自进化、不确定性量化、可靠停止、推理预算与 AI for Science。

**交付状态：可部署的第一版项目，而不是已经上线的服务。** 已通过离线单元测试和桌面/移动端页面交互检查。当前运行环境无法解析外部域名，因此采集适配器没有完成真实网络端到端联调；部署后必须查看首次运行日志与网页“来源状态”。样例论文的来源页面由浏览工具核对，但这不等于采集器已在线验证。

## 先看页面

直接打开根目录的 `preview.html`。这是单文件离线预览；部分浏览器或企业管理策略可能阻止本地脚本，此时按下面方法启动本地 HTTP 服务。

预览包含 6 篇有原始来源的真实论文样例，并清楚标注为人工样例，不代表 2026-10-16 的最新或完整检索结果。预览中的简短中文导读是人工整理，生产采集保留来源摘要，不会自动翻译。`data/preview.json` 不会被每日生产任务导入。

## 本地运行

要求 Python 3.11 或更新版本。测试环境为 Python 3.13；GitHub 工作流配置为 Python 3.12。

```bash
cd paper-radar
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v

# 无需联网，生成离线样例网页
python run.py --preview
python -m http.server 8080 --bind 127.0.0.1 --directory site
```

然后打开 `http://127.0.0.1:8080`。Windows PowerShell 的环境激活命令改为 `.\.venv\Scripts\Activate.ps1`。

真实采集时，在另一个终端中运行：

```bash
source .venv/bin/activate
# 可选：Crossref polite pool 使用真实联系邮箱；不是 API 密钥。
export CROSSREF_MAILTO="your-email@example.com"
python run.py
```

程序会更新 `data/store.json`、`data/state.json` 和 `site/index.html`。网页端不会跨域直接抓论文，也不会因浏览器关闭而中断服务器上的任务。

单独调试某类来源：

```bash
python run.py --source arxiv
python run.py --source Science
python run.py --source ICLR.cc
python run.py --build-only
```

返回码 1 表示至少一个来源失败，其他来源的数据和此前记录仍保留；返回码 2 表示未选中来源。未公开会集、初次详情回填未完成等情况会显示专门状态。

## 发布到 GitHub Pages

这里会创建公开可访问的网站，默认仓库也可设为公开。`data/` 中的论文元数据、研究关键词和核验配置会随仓库公开；不要放未公开手稿、账户凭据或个人敏感材料。是否使用私有仓库及对应 Pages 能力，应以你的 GitHub 计划与组织设置为准。

1. 创建你授权发布的仓库，默认分支使用 `main`，上传项目文件；务必包含隐藏目录 `.github/workflows/`。仓库创建与公开发布尚未由本项目执行。
2. 在 **Settings → Pages → Build and deployment → Source** 选择 **GitHub Actions**。
3. 确认仓库允许 Actions 运行，以及工作流具有写入 `contents` 和部署 Pages 的权限；组织限制或受保护分支可能阻止机器人保存数据。
4. 可在 **Settings → Secrets and variables → Actions** 添加 `CROSSREF_MAILTO`，值为真实联系邮箱。不需要付费大模型 API Key。
5. 进入 **Actions → Daily Paper Radar → Run workflow** 手动运行一次，检查每个来源日志。Pages 部署步骤会返回实际网址；不要在成功前把示例域名当作已部署地址。

默认每天 **北京时间 08:17** 运行，位置在 `.github/workflows/daily.yml`：

```yaml
schedule:
  - cron: '17 0 * * *'  # UTC 00:17，即北京时间 08:17
```

`config.yaml` 中的 `site.timezone` 只影响网页显示；要改运行时间，请修改工作流中的 UTC cron 表达式。这是配置，不是当前已启用的任务。

GitHub 官方明确说明：定时工作流可能延迟，繁忙时可能被丢弃；公共仓库长期无活动时也可能停用计划任务。不要把它当作保证准点的调度服务。本项目每天保存运行状态，有持续仓库写入，但仍需要关注任务失败通知和页面的陈旧提示。相关官方说明：
- https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule
- https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages

工作流使用独立 build、deploy 和 source-health 作业，先从正式档案重建网页，网页构建和发布不导入预览数据。它会在部署后报告采集失败，因此网页可保留旧记录并呈现失败来源，而不是直接消失。定时任务、Pages 本身和完整源站连接尚未在线联调。

## 已配置的来源与边界

| 来源 | 采集方式 | 默认范围与边界 |
|---|---|---|
| arXiv | 官方 Atom API | cs.AI / cs.CL / cs.LG / stat.ML / cs.CV / cs.RO；按主题查询、最近修改时间排序；单连接且请求间隔至少 3.2 秒 |
| Nature 系列 | 官方 RSS + Crossref 增量 | Nature、Nature Machine Intelligence、Nature Computational Science、Nature Communications |
| Science 系列 | 官方 RSS + Crossref 增量 | Science、Science Advances、Science Robotics |
| OpenReview | API v2 公共已录用集合 | ICLR、ICML、NeurIPS；当前年和前一年，**只接受精确匹配的正式 venueid**，不把公开投稿当录用 |
| PMLR | 官方卷索引与论文详情 | ICML、AISTATS、UAI、COLT 的当前年卷；自动发现卷号，不把固定卷号套到未来年份 |
| ACL Anthology | 官方仓库批量 XML | ACL、EMNLP、NAACL 当前年主会 long/short/main；不混入 Findings、workshops、tutorials、卷首材料 |
| CVF | 官方主会论文集与详情 | CVPR、ICCV 近期会集；不抓 workshop 集合 |

这不是所有“顶会”的全集。AAAI、IJCAI、ECCV、SIGGRAPH、ICRA 等未在第一版中默认接入；没有把它们虚标为已覆盖。即使已配置的会集也可能尚未公开、受访问策略影响或变更路径，页面会显示状态。

PMLR/CVF 默认**先筛标题、再抓候选详情**，因此可能漏掉标题不含关键词、但摘要相关的工作。`fetch.title_prefilter: false` 可关闭初筛，对已发现会集分批读取全部摘要；每会集每次默认 60 个详情页面，初次回填需要多次运行。`data/state.json` 保存队列进度。arXiv/OpenReview/ACL 的主题判断使用元数据中的标题和摘要；RSS/Crossref 没有摘要时只能按标题筛。

官方文档：
- arXiv API: https://info.arxiv.org/help/api/user-manual.html
- arXiv API 使用限制: https://info.arxiv.org/help/api/tou.html
- Nature RSS 说明示例: https://www.nature.com/nmeth/web-feeds
- Science feeds: https://www.science.org/content/page/email-alerts-and-rss-feeds
- Crossref 元数据与同步: https://www.crossref.org/documentation/retrieve-metadata/rest-api/tips-for-using-the-crossref-rest-api
- OpenReview 状态字段: https://docs.openreview.net/how-to-guides/data-retrieval-and-modification/how-to-get-all-notes-for-submissions-reviews-rebuttals-etc
- PMLR: https://proceedings.mlr.press/
- ACL 数据访问: https://aclanthology.org/info/development/
- CVF: https://openaccess.thecvf.com/

## “相关”如何定义

在 `config.yaml` 的 `topics` 中编辑英文检索词和权重。标题命中的权重为摘要命中的两倍，分数用于排序，不是质量、创新性、可信度或统计概率。默认最低分为 3；视觉/机器人作为低权重扩展方向，单一弱命中可能不会入库。

当前不使用语义向量或大模型筛选。关键词法可解释且不需要模型费用，但会有漏检/误检；“可靠停止”在扩散模型和智能体中可能不是同一问题，因此最终相关性需要阅读核查。仅切换网页主题筛选不会修改服务器采集关键词，必须编辑配置并重新运行。

## 版本与时间

- arXiv 版本号归一化；DOI、arXiv ID 优先去重；无稳定标识时仅在规范化标题与第一作者完全一致的情况下合并，不做宽松模糊合并。
- 题目变化大、第一作者格式不同、缺 DOI 的跨源版本可能仍保留为两条。没有声称百分之百合并。
- `published_at` 保留正式来源提供的日期精度；只有年/月就显示年/月，未知则明确显示未知。
- `preprint_at`、`first_seen_at`、`record_changed_at`、`source_updated_at` 分开记录。首次导入旧会集不会被伪装成今天发表。
- 初始 arXiv/期刊窗口 30 天；后续增量使用最后完整成功时间，回看 7 天补漏。跨来源永久档案按已有数据逐步累积；新增索引到的旧论文可以进入档案，而非伪装成新发表。
- 分页上限触发时不前移增量时间水位，并显示部分完成；应扩大上限或缩小主题，不能把截断结果当全量。

## 官方开源代码与“严选对比”

严选视图要求：已录用/已发表 + 明确为研究论文 + 有人工核验的官方开源代码证据。代码 URL 只要出现在摘要里，最多标为“待核验”，不自动认定为官方实现或具有开源许可证。

使用 `overrides.yaml` 记录核验结果，并附作者/出版方的来源页面。本项目附带一条 LACE 样例，证据来自出版社 Code availability 对官方代码及 MIT 许可的明确声明；并未实际运行代码或验证论文结果。

```yaml
- match: "doi:10.xxxx/example"
  document_type: research_article
  official_code: true
  code_url: "https://github.com/author/repository"
  evidence_url: "https://publisher.example/article#code-availability"
  checked_at: "2026-10-16"
  note: "人工核验官方归属和许可依据；未验证可复现性。"
```

该严格筛选并非完全自动核验服务。否则极易把第三方复现、空仓库、只写了 accepted 的预印本当成可靠对比方法。Crossref 的 `journal-article` 也可能涵盖新闻等内容，本项目对类型不明条目保留“待确认”，而不是强行标为研究论文。

## Linux 服务器日更

用系统的 cron 或 systemd timer 执行同一个 `python run.py` 即可。示例见 `deploy/README.md`。网页可由你已有的静态站点服务提供，不需要数据库服务器、Node 构建系统或大型 Agent 框架。文件存储采用 JSON，适合个人中小规模档案；大规模长期采集需另行规划存储、分页与索引。

## 测试与尚未验证部分

`python -m unittest discover -s tests -v` 包含原有 20 项采集测试及新增 13 项部署/脱敏测试，覆盖 Atom/RDF/XML/HTML 解析、状态分类、版本合并、日期精度、异常保留与脚本转义。测试使用小型人工构造 fixture，**不能证明当前源站的所有响应结构和网络权限都可用**。

浏览器检查覆盖桌面 1440px / 手机 390px、搜索/来源筛选、预印本/发表/严选视图、收藏交互、来源状态对话框和无页面横向溢出。由于测试浏览器禁止本地 URL，采用内存 HTML 渲染；跨刷新收藏持久性、真实下载传输未在该浏览器验证，详见 `TEST_REPORT.json`。

源站在线联调、GitHub 工作流首次运行及公网部署仍待完成。生产摘要来自来源元数据；只提供原文链接，不绕过付费墙，不抓取未经授权全文。公开部署前还应核对所用元数据许可和源站政策。
