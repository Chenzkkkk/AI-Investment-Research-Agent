"""Public defaults must not expose local history or require the upstream clone."""
import asyncio
import json
from pathlib import Path
from unittest.mock import patch
from streamlit.testing.v1 import AppTest
from investment_agent.config import Settings
from investment_agent.ashare_research import TradingAgentsModel,scenario_prompt,upstream_manifest
from investment_agent.public_screen import sector_condition

ROOT=Path(__file__).resolve().parents[1]

def test_native_model_without_upstream(tmp_path):
    async def complete(self,messages,allow_tools=True):
        assert not allow_tools and len(messages)==2
        return {'content':'证据支持的研究意见'}, {'total_tokens':5}
    with patch('investment_agent.agent.DeepSeekClient.complete',complete):
        model=TradingAgentsModel(Settings(root=tmp_path))
        assert asyncio.run(model.complete('system','evidence'))['usage']['total_tokens']==5
    assert upstream_manifest(tmp_path)['files']
    assert '乐观' in scenario_prompt(tmp_path,'bull')

def test_technology_discovery_scope_is_explicit():
    assert '半导体' in sector_condition('AI产业链')
    assert '自动化设备' in sector_condition('机器人')
    assert '软件开发' not in sector_condition('机器人')
    assert '半导体' in sector_condition('半导体行业')

def test_home_does_not_read_personal_history(tmp_path):
    path=tmp_path/'data/ashare/runs/2026-01-01-1234abcd'
    path.mkdir(parents=True)
    (path/'result.json').write_text(json.dumps({'reports':{'review':'PRIVATE_HISTORY_SENTINEL'}}))
    settings=Settings(root=tmp_path,enable_miaoxiang=False,enable_choice=False)
    with patch('investment_agent.config.Settings.load',return_value=settings):
        app=AppTest.from_file(str(ROOT/'app.py')).run(timeout=25)
        assert not app.exception
        assert app.radio[0].options==['发现','研究','情报']
        assert all('PRIVATE_HISTORY_SENTINEL' not in m.value for m in app.markdown)
        next(w for w in app.selectbox if w.label=='专业工作区').set_value('数据与技能').run(timeout=25)
        app.radio[0].set_value('研究对话').run(timeout=25)
        assert not app.exception and len(app.chat_input)==1
        assert next(w for w in app.selectbox if w.label=='本轮投资偏好').value=='value'

def test_public_example_has_real_links_and_dated_observations():
    path=ROOT/'examples/technology-research.json'
    if not path.exists(): return
    demo=json.loads(path.read_text(encoding='utf-8'))
    assert demo['eligible_count']<=demo['candidate_count']
    assert 'https://static.cninfo.com.cn/' in demo['review']
    assert '[E1]' not in demo['review']
    for row in demo['companies']:
        assert row['公告'] and row['行情日期']<=demo['cutoff']
        assert row['bars'] and max(b['date'] for b in row['bars'])<=demo['cutoff']
