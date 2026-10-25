# TradingAgents-CN 复用清单

审查版本：`51060a7682cf51810b4bc3a704829438d18cd8dc`。只提取和适配 Apache-2.0 目录中的角色方法；源文件哈希随 roles.json 保存。没有导入上游服务或复制受限目录。

| 角色 | 上游文件 | 本项目运行位置 |
|---|---|---|
| fundamental | `tradingagents/agents/analysts/fundamentals_analyst.py` | upstream_adapter / research_perspective；联合研究按角色调用 |
| technical | `tradingagents/agents/analysts/market_analyst.py` | upstream_adapter / research_perspective；联合研究按角色调用 |
| events | `tradingagents/agents/analysts/news_analyst.py` | upstream_adapter / research_perspective；联合研究按角色调用 |
| sentiment | `tradingagents/agents/analysts/social_media_analyst.py` | upstream_adapter / research_perspective；联合研究按角色调用 |
| bull | `tradingagents/agents/researchers/bull_researcher.py` | upstream_adapter / research_perspective；联合研究按角色调用 |
| bear | `tradingagents/agents/researchers/bear_researcher.py` | upstream_adapter / research_perspective；联合研究按角色调用 |
| review | `tradingagents/agents/managers/risk_manager.py` | upstream_adapter / research_perspective；联合研究按角色调用 |
| research_manager | `tradingagents/agents/managers/research_manager.py` | upstream_adapter / research_perspective；联合研究按角色调用 |

共8类角色模板进入运行链路。离线测试通过模拟模型验证调用位置；真实公开财报试运行调用了 bear 反证视角。风险评审会引用已有材料，不将多角色重复使用同一材料称为独立验证。

依赖：标准库JSON、本项目DeepSeek客户端。MongoDB模板存储和上游 core 配置依赖被移除；保留的只是经审查的文字方法。独立模板不等同于已经复用整套上游数据适配器、记忆系统或模型用量实现。后者由本项目实现并独立测试。

许可见 `investment_agent/upstream/LICENSE` 和 `NOTICE`；文件级哈希与修改说明见 `roles.json`。公开版本可在不存在外部clone的情况下运行。

妙想允许本地已安装并审查的基础分析、筛选、技术和可比公司入口；财报点评和行业研究通过MCP等价能力适配，明确标注调用方式。远端凭证/额度不等于本地Skill已下载，失败会保留错误并回落公开数据。未复制供应商脚本及凭证。

[上游许可声明](https://github.com/hsliuping/TradingAgents-CN/blob/v3.0/LICENSE)
