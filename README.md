# 知衡 · AI Investment Research Agent

**从技术变化到商业价值，用证据研究 AI、机器人与前沿科技。**

![知衡研究工作台](docs/preview.png)

一个可以在本机运行的中文投资研究工作台。用自然语言提出问题，Agent 扫描行业候选、核验财报与公告、比较盈利质量和估值，再给出有条件的研究判断。它也持续整理新产品、科研、公司动态与人物观点，为投资和创业研究提供线索。

## 三个入口

| 入口 | 做什么 |
| --- | --- |
| 发现 | 阅读科技价值研究示例、比较公司与查看 K 线 |
| 研究 | 输入模糊行业问题或明确股票，选择价值、成长、趋势等偏好 |
| 情报 | 浏览公开事件、来源、主题演进和关注更新 |

历史记录和量化工具收在「更多工具」。浏览示例无需模型凭证，也不会触发付费调用。公开示例是有截止日期的真实数据研究，不是实盘收益，也不代表全市场最优股票。

## 本地启动

使用独立 Conda 环境；已有同名环境时先检查并复用，不要重复创建或覆盖。

```bash
git clone https://github.com/Chenzkkkk/AI-Investment-Research-Agent.git
cd AI-Investment-Research-Agent
conda env create -f environment.yml
conda activate investment-agent
python -m streamlit run app.py
```

打开 http://127.0.0.1:8501 。Windows 可在已配置 Conda 的终端运行 `start_agent.bat`。

发起新研究前，把 `.env.example` 复制为 `.env`，只在本机填写自己的 DeepSeek 凭证。模型默认 `deepseek-flash`、推理强度 `high`；可按账号实际支持情况修改模型与地址。模型调用可能产生供应商费用。公开财报与行情默认启用；妙想为可选增强来源，需自己的权限和凭证。Choice 默认关闭，官方 SDK 与第三方 Skills 不随仓库分发。

## 可以这样问

- AI 产业链中有哪些值得长期研究的公司？先广泛筛选，再比较现金流、竞争优势和估值。
- 机器人从产品演示到规模商业化有哪些实质进展？哪些判断还缺证据？
- 分析汇川技术，区分已披露事实、增长假设与估值风险，并给出判断失效条件。

行业分类用于发现线索，不能证明每家公司的相关业务收入。Agent 可以得出「等待确认」或「证据不足」，不会为展示强行给出买入结论。

## 验证与结构

```bash
python -m pytest tests -q
python -m investment_agent.intelligence.worker
```

第二条命令执行一轮到期情报任务；只有需要本机定时更新时才另行配置计划任务。重新生成科技研究需模型凭证，可运行 `python scripts/build_technology_demo.py`，结果默认仍保存在本地，不会自动发布。

`investment_agent/` 包含数据适配、确定性计算、六阶段研究、情报库和界面；`tests/` 包含离线回归测试；`examples/` 仅包含审查过的公开示例。详见 [架构与研究口径](docs/architecture.md) 和 [数据边界](docs/data-policy.md)。

数据来自公开披露平台和已启用供应商，可能受网络、频率和授权限制。始终显示实际来源和日期；不补造缺失值。参考 [AKShare 官方文档](https://akshare.akfamily.xyz/)、[巨潮资讯](https://www.cninfo.com.cn/)、[DeepSeek 文档](https://api-docs.deepseek.com/)。界面使用 Streamlit / Plotly，情报采集使用 httpx / feedparser / Trafilatura。多视角分析思路参考 [TradingAgents-CN](https://github.com/hsliuping/TradingAgents-CN)；本仓库使用独立实现，不分发其代码、提示词或安装环境。

本项目用于研究辅助，不连接实盘交易。默认仅监听本机且没有面向公网的用户隔离；不要直接作为多用户托管服务部署。

## 财务建模与数据分析

在「更多工具 → 财务建模」输入股票代码与简称，读取公开三表和官方公告。支持年度/中期财务比较、现金资本开支、现金质量和杜邦ROE分解。对话Agent也可调用 `financial_model`，联合研究会自动纳入财务诊断。

FCFF DCF工作台提供逐年现金流、审慎/基准/积极情景及WACC—永续增长敏感性。必须填写假设依据并确认；默认输入是操作示例，不是公司指引。结果是经营企业价值，不直接冒充每股目标价。尚未实现完整三表联动预测、自动净债务桥接或银行保险专项模型。

公开财报路线失败时，会查询官方披露索引并尝试有限字段的PDF补取；取得原文、解析字段和核验数字分别记录。公司披露与程序解析成功是两件事，不会把取数失败写成公司未披露。方法参考 [NYU Damodaran的FCFF框架](https://pages.stern.nyu.edu/~adamodar/New_Home_Page/littlebook/cashflows.htm) 与 [CFI杜邦分析](https://corporatefinanceinstitute.com/resources/accounting/dupont-analysis/)。
