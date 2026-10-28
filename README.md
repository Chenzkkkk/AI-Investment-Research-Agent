# 知衡 · AI Investment Research Agent

**面向中国市场各行业的投资研究 Agent。**

![知衡产品首页](docs/preview.jpg)

一个用自然语言组织投研的本地 Demo：提出公司或行业问题，Agent 查找资料、调用分析方法，生成包含财务、估值、K 线和原始出处的研究报告。报告可回看、收藏及导出 PDF。投资风格默认价值投资，也支持成长、短线、事件驱动和综合研究。

产品支持跨行业研究；AI、机器人与前沿科技是本仓库选用的公开展示案例，不是产品的行业限定。具体覆盖程度取决于实际取得的数据和已验证的专业方法。

## 运行效果

以下为本地运行快照，部分增强尚未同步到公开源码；公开版能力及限制见下方说明与[能力清单](docs/professional-workspace.md)。截图用于展示研究流程，不是实时投资推荐。点击图片可查看大图。

### 投资分析 Agent

从问题与投资偏好出发，展示候选筛选、财务对比和判断依据。保留数据日期与来源，证据不足时允许暂不推荐。

![投资分析报告中的公司筛选结论、财务指标比较及证据链接](docs/investment-report.jpg)

### K 线与公司比较

日 K、成交量、移动均线与公司比较在同一看板呈现；未取得的维度留空，不按零计算。

![投资分析数据看板中的 K 线、成交量、均线及公司比较](docs/market-dashboard.jpg)

### 前沿情报 Agent

围绕一个主题融合多来源材料，形成中文综合报告，关联产品发布、企业应用与创业融资，并保留逐项原始出处。

![前沿情报 Agent 对 AI 智能体商业化的中文综合分析与原始出处](docs/intelligence-report.jpg)

## 主要能力

- **对话研究**：持久化会话、后台任务、停止与中断恢复；桌面对话区与可收起证据侧栏约为 7:3。
- **完整研究报告**：对话中显示结论节选与阅读入口；独立报告页保留正文、财务趋势、K 线、股票对比、原文与覆盖检查。只展示本次保存的数据，缺失图表明确提示。
- **历史与导出**：报告自动保存本机，支持搜索、收藏、回看与 PDF / Markdown 导出；PDF 包含中文正文、可用图表、财务明细、覆盖记录与原文链接。网页互动不调用模型，导出也不请求新数据。
- **专业方法**：14 个版本化 Skills，涵盖财务质量、资本配置、三表情景、估值、统计、组合、政策、治理与反证。公式交给 Python，解释交给模型。
- **公司财务**：公开三表及官方年报补查，累计季度拆分与 TTM、杜邦、现金质量和附注线索。三表情景、DCF 敏感性与股权桥接可编辑重算。
- **行业研究**：候选池上限 500、初筛比较前 20、深研最多 5；显示实际取得数量、日期及淘汰原因，不宣称覆盖全市场。
- **前沿情报**：八个领域的事件、来源、人物观点、主题演进及关注；与股票研究共用证据。「全部材料」按来源、时间与领域筛选和分页，保留原文链接；任何文章均可手动选择生成中文简报，推荐精选不会隐藏其余材料。
- **多视角审阅**：适配 TradingAgents-CN 的 8 类 Apache-2.0 角色方法，按任务选择，而非每次运行全部角色。

数据不足时允许“暂不推荐”。研究覆盖不代表成功概率；计算通过也不表示假设已验证。当前为单用户本地应用，不是公网多租户服务。

## 我怎样用 vibe coding 做这个 Demo

我提出研究需求、检查界面和报告，再通过 **Codex 与 Kimi** 迭代功能、数据流程和测试。运行时使用已配置的 DeepSeek 做资料归纳、研究规划与解释；财务公式、指标和校验由 Python 执行。TradingAgents-CN 的可复用角色方法经适配后接入研究流程，许可与实际复用范围见下文文档。

Kimi 的专业数据能力也是探索方向。其官方插件目录包括同花顺、天眼查等数据库，可用于金融与企业信息研究；**这是 Kimi 平台的能力说明，仓库不附带这些付费数据库的账号、授权或完整连接服务**。本项目默认走公开数据路线。[Kimi 官方插件说明](https://www.kimi.com/help/kimi-work/plugin-center)

研究流程：**问题与偏好 → 资料及数据 → 专业方法与反证 → 可追溯报告 → 历史复盘**。

## 启动

```powershell
git clone https://github.com/Chenzkkkk/AI-Investment-Research-Agent.git
cd AI-Investment-Research-Agent
conda env create -f environment.yml
conda activate investment-agent
# Windows PowerShell：先创建本地配置，再填写自己的模型凭证
if (!(Test-Path .env)) { Copy-Item .env.example .env }
# 用本地编辑器填写 .env 中的 DEEPSEEK_API_KEY
python -m streamlit run app.py
```

已有 `investment-agent` 环境时先检查并复用，再在该环境执行 `python -m pip install -r requirements.txt`。不要安装到 base。

浏览 http://127.0.0.1:8501 。上述复制命令适用于 Windows PowerShell；macOS/Linux 使用 `cp .env.example .env`。已有 `.env` 时保留原配置。DeepSeek 默认 `deepseek-flash`、推理 `high`，推理强度可在网页切换；模型和地址以账号支持情况为准。模型调用可能产生供应商费用。仓库没有预置密钥，首次运行需要配置自己的凭证。

公开数据默认启用，妙想可选并需自己的权限，Choice 默认关闭。第三方 SDK、账号激活文件、妙想供应商脚本和私有数据不随仓库分发。读取已有报告或浏览情报不触发模型。

## 可以这样问

- 机器人产业哪些公司已实现商业化？比较现金流、竞争、估值和风险。
- 分析工业富联的财务质量，指出我最容易忽略的三个问题。
- 最近的新产品中，哪些可能改变小企业的工作方式？保留原始出处。
- 我的研究组合集中在哪些行业？按我提供的真实持仓和收益数据做压力情景。

## 验证与边界

```bash
python -m pytest tests -q
python -m investment_agent.intelligence.worker
```

第二条命令只执行一轮到期情报任务；定时采集需另行配置。研究任务在本地网页服务存活时后台执行，服务重启后显式继续；不是云端常驻执行。会话数据库、研究快照和导出文件都在本机 `data/` 下；自动保存不是云备份，不应删除该目录。

当前可执行范围及限制见 [专业能力与验收](docs/professional-workspace.md)、[架构](docs/architecture.md)、[数据边界](docs/data-policy.md)。金融企业估值、扫描财报 OCR、完整历史修订库和全市场无幸存者偏差评估尚未完成。PDF 解析是有限字段和页数的辅助流程，不能等同于完整审计。

基础设施为 Streamlit、Plotly、LangGraph、SQLite WAL、DuckDB、httpx、feedparser、Trafilatura、pdfplumber、ReportLab。PDF 优先使用本机中文字体，缺少字体时回退到标准 CJK 字体；不再分发系统字体。首页主视觉为项目生成的概念图，不是行情数据。免费路线使用已验证公开接口及 AKShare/BaoStock；公开可访问不等于有保证的官方 API 或可任意再分发。

方法覆盖参考 [CFA CBOK](https://www.cfainstitute.org/programs/cfa-program/candidate-resources/cbok)，不宣称 CFA 认证。适配的上游方法详见 [许可与复用清单](docs/upstream-reuse.md)、[TradingAgents-CN](https://github.com/hsliuping/TradingAgents-CN)。财务计算为本项目依据公开方法独立实现。

本项目用于研究，不连接交易执行。个人问题、报告、账户和数据库保留本地；公开科技示例为单独审查的有日期研究，不能视为实时推荐或投资收益。

## 隐私与分享

`.env`、令牌、日志、账户文件、本地数据与「分享」目录均不属于公开交付内容。运行后的报告和情报库保存在本地 `data/`；请不要强制提交忽略文件。公开展示案例与个人研究档案分别管理。向 GitHub 发布代码不会上传本机数据库，也不会自动托管网页。

公开的是已审查的轻量研究应用，不包含上游受限目录、供应商 SDK 或付费数据授权。部署到互联网前仍需补充身份认证、用户隔离和服务端密钥管理。
