import asyncio
from dataclasses import replace
from types import SimpleNamespace

import pandas as pd
import pytest

from investment_agent.agent import ResearchAgent
from investment_agent.choice import ChoiceProvider
from investment_agent.common import DataError, Result, number, safe, symbol, quote_from_mcp
from investment_agent.config import Settings
from investment_agent.service import DataService, compare_quotes, screening_query, verify_screen_rows
from investment_agent.miaoxiang import MCP_TOOLS, MiaoxiangProvider
from investment_agent.public import PublicDataProvider


def settings(tmp_path, **kwargs):
    return Settings(root=tmp_path, **kwargs)


@pytest.mark.parametrize("raw,expected", [("600519", "600519.SH"), ("000001", "000001.SZ"), ("920001", "920001.BJ"), ("430047.bj", "430047.BJ")])
def test_codes(raw, expected):
    assert symbol(raw) == expected


def test_secrets_removed_recursively():
    secret = "unit-test-secret"
    value = safe({"api_key": secret, "data": ["echo " + secret], "password": "x"}, (secret,))
    assert secret not in str(value) and "api_key" not in value


def test_choice_disabled_does_not_import_or_login(tmp_path):
    def forbidden():
        raise AssertionError("SDK must not load")
    provider = ChoiceProvider(settings(tmp_path), loader=forbidden)
    with pytest.raises(DataError, match="已关闭"):
        provider.request("quote")


def test_choice_auth_denial_is_circuit_broken(tmp_path):
    calls = []
    sdk = SimpleNamespace(start=lambda *a, **k: (calls.append("login") or SimpleNamespace(ErrorCode=10001003)),
                          stop=lambda: calls.append("stop"))
    provider = ChoiceProvider(settings(tmp_path, enable_choice=True), loader=lambda: sdk)
    with pytest.raises(DataError) as error:
        provider.request("quote")
    assert error.value.code == "CHOICE_10001003"
    with pytest.raises(DataError) as error:
        provider.request("quote")
    assert error.value.code == "CHOICE_AUTH_BLOCKED"
    assert calls == ["login"]


def test_choice_authorized_uses_verified_indicators_and_logout(tmp_path):
    calls = []
    sdk = SimpleNamespace(start=lambda *a, **k: SimpleNamespace(ErrorCode=0),
        css=lambda *a: (calls.append(a) or pd.DataFrame({"PETTM": [20.], "PBMRQN": [5.]}, index=["600519.SH"])),
        stop=lambda: calls.append("logout"))
    provider = ChoiceProvider(settings(tmp_path, enable_choice=True), loader=lambda: sdk)
    result = provider.request("fundamentals")
    assert result.ok and calls[0][1] == "PETTM,PBMRQN" and calls[-1] == "logout"
    assert result.data_time is None  # Must not invent timestamps from requested dates.


def test_choice_logout_even_when_query_raises(tmp_path):
    calls = []
    def broken(*args):
        raise OSError("DLL query failed")
    sdk = SimpleNamespace(start=lambda *a, **k: SimpleNamespace(ErrorCode=0), csqsnapshot=broken,
                          stop=lambda: calls.append("logout"))
    with pytest.raises(OSError):
        ChoiceProvider(settings(tmp_path, enable_choice=True), loader=lambda: sdk).request("quote")
    assert calls == ["logout"]


def test_no_trade_access(tmp_path):
    for name in ("porder", "pctransfer", "pquery", "delete", "__class__"):
        with pytest.raises(DataError) as error:
            ChoiceProvider(settings(tmp_path)).request(name)
        assert error.value.code == "TOOL_NOT_ALLOWED"


@pytest.mark.parametrize("operation,expected", [("quote", "csqsnapshot"), ("history", "csd"), ("calendar", "tradedates"),
    ("news", "cfn"), ("screen", "cps"), ("report", "ctr"), ("macro", "edb"), ("usage", "datastatistics")])
def test_choice_readonly_operations_ready_after_enable(tmp_path, operation, expected):
    calls = []
    (tmp_path / "data").mkdir()
    (tmp_path / "data/choice_edb_catalog.json").write_text('{"fixture-id":{"unit":"fixture-unit"}}', encoding="utf-8")
    def response(*args):
        calls.append(args)
        return SimpleNamespace(ErrorCode=0, Data={"600519.SH": [100, "150000"]},
                               Codes=["600519.SH"], Indicators=["NOW", "TIME"], Dates=[])
    sdk = SimpleNamespace(start=lambda *a, **k: SimpleNamespace(ErrorCode=0), stop=lambda: calls.append("logout"),
                          **{expected: response})
    result = ChoiceProvider(settings(tmp_path, enable_choice=True), loader=lambda: sdk).request(operation, indicator="fixture-id")
    assert result.ok and calls[-1] == "logout"


def test_budget_cache_and_disabled_cache(tmp_path):
    service = DataService(settings(tmp_path, max_calls=1))
    calls = []
    async def action():
        calls.append(1)
        return Result(source="miaoxiang_mcp", data={"price": 1})
    async def run():
        a = await service.fetch("miaoxiang", {"a": 1}, action)
        b = await service.fetch("miaoxiang", {"a": 1}, action)
        c = await service.fetch("miaoxiang", {"a": 2}, action)
        service.settings.enable_miaoxiang = False
        d = await service.fetch("miaoxiang", {"a": 1}, action)
        return a, b, c, d
    a, b, c, d = asyncio.run(run())
    assert a.ok and b.cached and len(calls) == 1
    assert c.error["code"] == "BUDGET_EXHAUSTED"
    assert d.error["code"] == "DISABLED"


def test_failed_request_is_not_repeated_in_one_research(tmp_path):
    service = DataService(settings(tmp_path))
    calls = []
    async def fail():
        calls.append(1)
        raise OSError("Unavailable")
    async def run():
        await service.fetch("miaoxiang", {"q": "same"}, fail)
        return await service.fetch("miaoxiang", {"q": "same"}, fail)
    result = asyncio.run(run())
    assert len(calls) == 1 and result.metadata["duplicate_failure_suppressed"]


def test_disabled_miaoxiang_and_public_no_network(tmp_path):
    cfg = settings(tmp_path, enable_miaoxiang=False, enable_public=False)
    with pytest.raises(DataError) as error:
        asyncio.run(MiaoxiangProvider(cfg).list_tools())
    assert error.value.code == "DISABLED"
    with pytest.raises(DataError) as error:
        asyncio.run(PublicDataProvider(cfg).request("600519.SH"))
    assert error.value.code == "DISABLED"


def test_all_eleven_financial_mcp_tools_registered(tmp_path):
    assert len(MCP_TOOLS) == 11
    agent = ResearchAgent(settings(tmp_path))
    seen = []
    async def mcp(name, query):
        seen.append(name)
        return Result(source="fixture_mcp", data={"value": 1})
    agent.service.mx.mcp = mcp
    async def run():
        for name in MCP_TOOLS:
            agent.service.calls = 0  # Independent request budgets; no remote calls.
            r = await agent.dispatch("miaoxiang_query", {"name": name, "query": "bounded test"})
            assert r["ok"]
    asyncio.run(run())
    assert set(seen) == MCP_TOOLS


def test_fallback_records_failed_source(tmp_path):
    service = DataService(settings(tmp_path, enable_public=True))
    async def denied(*args, **kwargs):
        raise DataError("AUTH_REQUIRED", "Authorization missing")
    async def public(*args, **kwargs):
        return Result(source="public", data={"price": 123}, data_time=None)
    service.mx.mcp = denied
    service.public.request = public
    result = asyncio.run(service.stock("600519"))
    assert result.ok and result.source == "public"
    assert result.metadata["fallback_attempts"][0]["source"] == "miaoxiang"
    assert result.data_time is None


def test_time_and_basis_comparison_preserves_conflicts():
    def quote(source, price, timestamp):
        return Result(source=source, data={"price": price, "unit": "元/股", "currency": "CNY", "price_basis": "最新价"}, data_time=timestamp)
    a = quote("a", 100, "2026-10-19 14:00")
    b = quote("b", 101, "2026-10-19 14:00")
    assert compare_quotes([a, b])["comparisons"][0]["conflict"]
    b.data_time = None
    r = compare_quotes([a, b])
    assert not r["comparisons"][0]["comparable"] and len(r["results"]) == 2


def test_latest_quote_is_preferred_to_historical_close():
    raw = {"data": [{"columns": ["贵州茅台600519.SH", "2026-10-16"], "items": [["收盘价", "100"]]},
                    {"columns": ["贵州茅台600519.SH", "2026-10-19 11:00"], "items": [["最新价", "102"]]}]}
    assert quote_from_mcp(raw, "600519.SH")["price"] == 102


def test_no_financial_evidence_cannot_emit_invented_price(tmp_path):
    class Client:
        async def complete(self, messages, allow_tools=True):
            return {"role": "assistant", "content": "编造的价格9999元"}, {}
    result = asyncio.run(ResearchAgent(settings(tmp_path), client=Client()).ask("最新股价"))
    assert "9999" not in result["report"] and "未取得" in result["report"]


def test_screen_requires_all_years_and_unit():
    plan = {"min_roe_pct": 10, "max_pe_ttm": 30, "min_daily_amount_cny": 1e8}
    row = {"2023年ROE": "20%", "2024年ROE": "15%", "2025年ROE": "12%", "PE(TTM)": "22", "成交额(元)": "1.5亿"}
    result = verify_screen_rows([row], plan, [2023, 2024, 2025])
    assert result[0]["status"] == "verified"
    row.pop("2024年ROE")
    assert verify_screen_rows([row], plan, [2023, 2024, 2025])[0]["status"] == "unverified"
    row["2023年ROE"] = "5%"
    assert verify_screen_rows([row], plan, [2023, 2024, 2025])[0]["status"] == "failed"


def test_vendor_screen_period_unit_and_date():
    plan = {"trade_date": "2026-10-19"}
    row = {"2023年ROE(%)": "12.24|2023年报", "2024年ROE(%)": "11.48|2024年报", "2025年ROE(%)": "10.85|2025年报",
           "市盈率(TTM)": "9.87", "成交额(亿元) 2026.10.19": "3.48", "ST股票": "否"}
    row["2023.09.30 ROE(%)"] = "8.2|2023三季报"
    assert verify_screen_rows([row], plan, [2023, 2024, 2025])[0]["status"] == "verified"
    row["成交额(亿元) 2026.10.16"] = row.pop("成交额(亿元) 2026.10.19")
    assert verify_screen_rows([row], plan, [2023, 2024, 2025])[0]["status"] == "failed"


def test_screen_bounds_and_years():
    query, years = screening_query({})
    assert len(years) == 3 and years == sorted(years) and "前5只" in query
    with pytest.raises(DataError):
        screening_query({"limit": 500})


def test_deepseek_tool_roundtrip(tmp_path):
    class Client:
        n = 0
        async def complete(self, messages, allow_tools=True):
            self.n += 1
            if self.n == 1:
                return {"role": "assistant", "content": None, "tool_calls": [{"id": "call1", "type": "function",
                    "function": {"name": "stock_data", "arguments": '{"code":"600519","kind":"quote"}'}}]}, {}
            assert messages[-1]["role"] == "tool" and messages[-1]["tool_call_id"] == "call1"
            return {"role": "assistant", "content": "报价来自本轮数据[S1]。"}, {}
    service = DataService(settings(tmp_path))
    async def stock(**kwargs):
        return Result(source="fixture", data={"price": 100}, data_time="2026-10-19")
    service.stock = stock
    result = asyncio.run(ResearchAgent(settings(tmp_path), client=Client(), service=service).ask("查询600519"))
    assert len(result["trace"]) == 1 and result["trace"][0]["result"]["ok"]
    assert result["trace"][0]["arguments"]["code"] == "600519"
    assert "source=S1" in result["report"] and "[S1]" not in result["report"]


def test_unknown_tool_and_skill_path_traversal(tmp_path):
    agent = ResearchAgent(settings(tmp_path))
    for name, args in [("shell", {"command": "whoami"}), ("read_skill", {"name": "../../.env"})]:
        with pytest.raises(DataError):
            asyncio.run(agent.dispatch(name, args))
