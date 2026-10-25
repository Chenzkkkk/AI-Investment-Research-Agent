import asyncio
import json
from types import SimpleNamespace
from unittest.mock import patch
import httpx
import pytest
from investment_agent.agent import DeepSeekClient, ResearchAgent
from investment_agent.common import DataError, Result
from investment_agent.config import Settings


def test_default_model_uses_official_alias_and_high_thinking():
    cfg=Settings()
    assert cfg.model=="deepseek-flash" and cfg.reasoning_effort=="high"
    assert cfg.max_output_tokens==16384
    assert Settings(model="DeepSeek-V4.1-Flash-1017").model=="deepseek-flash"
    with pytest.raises(ValueError,match="REASONING_EFFORT"):
        Settings(reasoning_effort="made-up-level")


def test_client_payload_enables_high_reasoning_for_tools_and_plain_requests():
    captured=[]
    async def post(self,url,**kwargs):
        captured.append(kwargs["json"])
        return httpx.Response(200,json={"model":"deepseek-flash","choices":[{"finish_reason":"stop",
            "message":{"role":"assistant","content":"config-ok","reasoning_content":"opaque test fixture"}}],"usage":{}})
    with patch("httpx.AsyncClient.post",post):
        client=DeepSeekClient(Settings(deepseek_key="synthetic-placeholder"))
        for tools in (True,False):
            asyncio.run(client.complete([{"role":"user","content":"synthetic test"}],allow_tools=tools))
    for body in captured:
        assert body["model"]=="deepseek-flash" and body["thinking"]=={"type":"enabled"}
        assert body["reasoning_effort"]=="high" and body["max_tokens"]==16384
    assert "tools" in captured[0] and "tools" not in captured[1]
    assert client.last_response_metadata["reasoning_content_present"]


@pytest.mark.parametrize("effort",["low","high","max"])
def test_selected_reasoning_effort_is_sent_to_api(effort):
    async def post(self,url,**kwargs):
        assert kwargs["json"]["reasoning_effort"]==effort
        assert kwargs["json"]["thinking"]=={"type":"enabled"}
        return httpx.Response(200,json={"choices":[{"finish_reason":"stop","message":{"role":"assistant","content":"synthetic test"}}]})
    with patch("httpx.AsyncClient.post",post):
        asyncio.run(DeepSeekClient(Settings(deepseek_key="synthetic-placeholder",reasoning_effort=effort)).complete([]))


def test_ui_defaults_to_high_and_applies_selected_effort(tmp_path,monkeypatch):
    from pathlib import Path
    from streamlit.testing.v1 import AppTest
    observed=[]
    monkeypatch.setattr(Settings,"load",lambda:Settings(root=tmp_path,enable_choice=False,enable_miaoxiang=False,enable_public=False))
    async def ask(self,question,**kwargs):
        observed.append(self.settings.reasoning_effort)
        return {"report":"离线模型配置测试","model":self.settings.model,"created_at":"2026-10-21T16:00:00+08:00",
                "reasoning_effort":self.settings.reasoning_effort,"data_calls":0,"trace":[]}
    monkeypatch.setattr(ResearchAgent,"ask",ask)
    app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/"app.py")).run(timeout=20)
    app.radio[0].set_value('研究对话').run(timeout=20)
    selector=next(widget for widget in app.selectbox if widget.label=="推理强度")
    assert selector.value=="high"
    selector.set_value("max").run(timeout=20)
    app.chat_input[0].set_value("离线配置测试").run(timeout=20)
    assert not app.exception and observed==["max"]
    assert any("推理 max" in caption.value for caption in app.caption)
    next(widget for widget in app.selectbox if widget.label=="推理强度").set_value("low").run(timeout=20)
    app.chat_input[0].set_value("再次检查配置").run(timeout=20)
    assert not app.exception and observed==["max","low"]


def test_truncated_reasoning_cannot_be_reported_as_complete():
    async def post(self,url,**kwargs):
        return httpx.Response(200,json={"choices":[{"finish_reason":"length","message":{"role":"assistant","content":""}}]})
    with patch("httpx.AsyncClient.post",post),pytest.raises(DataError,match="输出预算"):
        asyncio.run(DeepSeekClient(Settings(deepseek_key="synthetic-placeholder")).complete([]))


def test_thinking_tool_roundtrip_retains_reasoning_without_persisting_it(tmp_path):
    opaque="private synthetic reasoning marker"
    class Client:
        n=0
        async def complete(self,messages,allow_tools=True):
            self.n+=1
            if self.n==1:
                # Past reports have no stored reasoning: they must be background summaries.
                assert not any(m["role"]=="assistant" for m in messages)
                assert any("历史对话摘要" in m.get("content","") for m in messages)
                return {"role":"assistant","content":None,"reasoning_content":opaque,
                        "tool_calls":[{"id":"probe-call","type":"function","function":{"name":"stock_data","arguments":'{"code":"600519","kind":"quote"}'}}]},{}
            assert next(m for m in messages if m["role"]=="assistant")["reasoning_content"]==opaque
            return {"role":"assistant","content":"合成测试，不是真实行情[S1]。","reasoning_content":opaque},{}
    async def stock(**kwargs):
        return Result(source="synthetic-test",data={"price":100})
    service=SimpleNamespace(stock=stock,calls=0,errors=[])
    result=asyncio.run(ResearchAgent(Settings(root=tmp_path),client=Client(),service=service).ask("合成测试",history=[
        {"role":"user","content":"历史问题"},{"role":"assistant","content":"历史报告，未保存推理原文"}]))
    assert result["thinking_enabled"] is True and result["reasoning_effort"]=="high"
    assert opaque not in json.dumps(result)
