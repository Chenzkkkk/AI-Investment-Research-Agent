import asyncio
from datetime import timedelta
import json
import os
from pathlib import Path
import re
import sys

from .common import DataError, Result, quote_from_mcp, safe, unpack_mcp
from .config import Settings

MCP_URL = "https://mxapi.eastmoney.com/mxds/mcp"
MCP_TOOLS = {
    "mx_ashare_finance_data", "mx_bond_finance_data", "mx_comprehensive_finance_data",
    "mx_finance_search_news", "mx_finance_search_notice", "mx_fund_finance_data",
    "mx_hk_finance_data", "mx_index_block_finance_data", "mx_macro_data",
    "mx_stocks_screener", "mx_us_finance_data",
}


def check_business_error(data):
    """MCP may return HTTP/tool success containing a quota/auth business failure."""
    if isinstance(data,dict):
        for key,value in data.items():
            if key in {'message','error','msg','errorMsg'} and isinstance(value,str):
                if any(s in value for s in ('积分已用完','积分不足','额度不足','额度已用完')):
                    raise DataError('MIAOXIANG_QUOTA','妙想提示积分或额度已用完；已停止本轮重复调用。未自动购买。')
                if any(s in value for s in ('鉴权失败','授权失效','未授权','无效的API')):
                    raise DataError('MIAOXIANG_AUTH','妙想凭证或权限失效，请在官方渠道处理。')
            if isinstance(value,(dict,list)): check_business_error(value)
    elif isinstance(data,list):
        for value in data: check_business_error(value)


class MiaoxiangProvider:
    def __init__(self, settings: Settings):
        self.settings = settings

    def _require(self, key: str) -> None:
        if not self.settings.enable_miaoxiang:
            raise DataError("DISABLED", "妙想数据源已关闭，不会发起请求。")
        if not key:
            raise DataError("MISSING_CREDENTIAL", "缺少妙想本地凭证。")

    async def mcp(self, name: str, query: str, code: str | None = None, kind: str = "research") -> Result:
        self._require(self.settings.mcp_key)
        if name not in MCP_TOOLS:
            raise DataError("TOOL_NOT_ALLOWED", "不允许调用该 MCP 工具。")
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client

        async with streamablehttp_client(MCP_URL, headers={"em_api_key": self.settings.mcp_key},
                                         timeout=timedelta(seconds=30),
                                         sse_read_timeout=timedelta(seconds=90)) as streams:
            async with ClientSession(streams[0], streams[1]) as session:
                await session.initialize()
                result = await session.call_tool(name, {"query": query})
        if result.isError:
            raise DataError("MCP_TOOL_ERROR", "妙想工具返回失败，请检查权限或查询范围。")
        data = safe(unpack_mcp([b.model_dump() for b in result.content]), (self.settings.mcp_key,))
        check_business_error(data)
        if data is None or data == [] or data == {} or data == "":
            raise DataError("EMPTY_DATA", "妙想未返回有效数据。")
        normalized = quote_from_mcp(data, code) if code else None
        return Result(source="miaoxiang_mcp", data=data, symbol=code, kind=kind,
                      data_time=normalized.get("data_time") if normalized else None,
                      metadata={"tool": name, "normalized_quote": normalized, "cost": "依账户权限计费，金额未知"})

    async def list_tools(self) -> list[str]:
        self._require(self.settings.mcp_key)
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client
        async with streamablehttp_client(MCP_URL, headers={"em_api_key": self.settings.mcp_key}) as streams:
            async with ClientSession(streams[0], streams[1]) as session:
                await session.initialize()
                result = await session.list_tools()
        return [t.name for t in result.tools]

    async def skill(self, name: str, query: str, stock_name: str = "") -> Result:
        self._require(self.settings.skills_key)
        routes={'stock-earnings-review':'mx_ashare_finance_data','industry-deep-research':'mx_index_block_finance_data'}
        if name in routes:
            result=await self.mcp(routes[name],query+'；返回原始数据、实际报告期和来源链接。',kind='research')
            result.metadata.update(requested_skill=name,execution_mode='reviewed_mcp_equivalent',
                limitation='使用已授权MCP等价数据路线，不冒充原生付费报告生成接口。')
            return result
        if name not in {"stock-fundamental-analysis", "mx-stocks-screener", "stock-technical-analysis", "comparable-company-analysis"}:
            raise DataError("SKILL_NOT_EXECUTABLE", "该 Skill 尚未审查运行入口，可读取说明或改用 MCP。")
        execution_query = query
        if name == "stock-fundamental-analysis":
            if not re.search("基本面|财务|财报|业绩|营收|利润|盈利|成长|现金流|估值|市盈率|市净率|ROE|经营", query, re.I):
                raise DataError("SKILL_INTENT_MISMATCH", "当前问题没有明确基本面研究意图。")
            if not re.fullmatch(r"[\u4e00-\u9fffA-Za-z*]{2,15}", stock_name):
                raise DataError("STOCK_NAME_REQUIRED", "基本面 Skill 需要单只 A 股中文名称；先按代码查名称。")
            execution_query = stock_name + "的基本面和估值"
        private_home = self.settings.root / "data/skill_home"
        private_home.mkdir(parents=True, exist_ok=True)
        env = {k: v for k, v in os.environ.items()
               if not re.search(r"(?:API_KEY|TOKEN|PASSWORD)$", k, re.I)}
        env.update({"EM_API_KEY": self.settings.skills_key, "PYTHONUTF8": "0", "PYTHONIOENCODING": "utf-8",
                    "USERPROFILE": str(private_home), "HOME": str(private_home)})
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-X", "utf8=0", str(self.settings.root / "scripts/skill_worker.py"), name,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            cwd=str(self.settings.root), env=env)
        try:
            stdout, _ = await asyncio.wait_for(process.communicate(json.dumps({"query": execution_query}).encode()), 100)
        except asyncio.TimeoutError:
            process.kill()
            await process.communicate()
            raise DataError("TIMEOUT", "Skill 请求超时，未自动重试。") from None
        try:
            response = safe(json.loads(stdout.decode("utf-8")), (self.settings.skills_key,))
        except (ValueError, UnicodeError):
            raise DataError("SKILL_PROTOCOL_ERROR", "Skill 未返回有效结果；原始输出未写入日志。") from None
        if not response.get("ok"):
            raise DataError(response.get("code", "SKILL_FAILED"), response.get("message", "Skill 调用失败"))
        return Result(source="miaoxiang_skill", kind="fundamentals" if name.startswith("stock-") else "screen",
                      data=response["data"], metadata={"skill": name, "execution_query": execution_query,
                      "original_query": query, "artifacts": response.get("artifacts", []),
                      "cost": "依账户权限计费，金额未知"})
