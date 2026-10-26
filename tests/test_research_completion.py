import asyncio
from copy import deepcopy
from pathlib import Path
import pytest
from investment_agent.professional_skills import CoverageChecklist
from investment_agent.reader_evidence import originals,reader_report,readable_url
from investment_agent.report_presentation import chat_links
from investment_agent.capital_bridge import bridge_scenarios
from investment_agent.common import DataError
from investment_agent.intelligence.research_agent import validate_brief,brief_markdown


def test_report_repairs_missing_table_header_separator():
    from investment_agent.report_presentation import normalize_markdown
    text='| 项目 | 数值 |\n| 收入 | 100 |\n| 利润 | 10 |'
    assert normalize_markdown(text).splitlines()[1]=='| --- | --- |'
    assert normalize_markdown(normalize_markdown(text))==normalize_markdown(text)
    assert normalize_markdown('```\n'+text+'\n```')=='```\n'+text+'\n```'


def test_coverage_tracks_computation_without_calling_it_cross_verified():
    c=CoverageChecklist()
    trace=[{'id':'S1','tool':'financial_model','result':{'ok':True,'data':{'analysis':{'rows':[{'period':'2025-12-31'}]},'dcf':{'scenarios':{'base':{'enterprise_value':100}}}}}}]
    c.observe(trace)
    assert c.items['financial']['status']==c.items['valuation']['status']=='calculated'
    assert c.items['financial']['evidence_ids']==['S1']
    assert c.summary()['quality_gate']=='incomplete'
    c.update([{'factor':'financial','status':'checked','claim':'金额已与原文核对','evidence_ids':['S1']}],trace)
    c.observe(trace)
    assert c.items['financial']['status']=='checked'


def test_failed_or_empty_research_is_not_counted_as_completed():
    c=CoverageChecklist()
    c.observe([{'id':'S1','tool':'execute_professional_skill','arguments':{'name':'governance'},'result':{'ok':True,'data':{'skill':'governance','data':{'evidence_status':'missing'}}}}])
    assert c.items['governance']['status']=='missing'
    assert c.items['valuation']['status']=='not_run'


def test_original_article_link_and_multi_source_readable_route(tmp_path):
    result={'report_file':str(tmp_path/('a'*32+'.md')),'report':'结论[S1]\n\n---\n数据证据\n\n- [S1] search_intelligence; 缓存 False','trace':[
        {'id':'S1','tool':'get_intelligence_event','result':{'ok':True,'source':'local_intelligence','data':{'articles':[{'title':'公司正式发布','url':'https://example.org/news/product'}]}}}]}
    links=chat_links(result,tmp_path)
    assert links['S1']==('公司正式发布','https://example.org/news/product')
    text=reader_report(result,tmp_path)
    assert 'search_intelligence' not in text and '缓存 False' not in text and 'https://example.org/news/product' in text
    result['trace'][0]['result']['data']['articles'].append({'title':'独立报道','url':'https://press.example.org/story'})
    assert 'source=S1' in chat_links(result,tmp_path)['S1'][1]
    assert not readable_url('https://datacenter-web.eastmoney.com/api/data/v1?report=x')
    assert not readable_url('https://emweb.securities.eastmoney.com/PC_HSF10/NewFinanceAnalysis/lrbAjaxNew?code=SH600001')
    assert not readable_url('https://example.com/a?token=secret')


def packet():
    facts={k:{'value':v} for k,v in {'cash_equivalents':30,'minority_interest':5,'shares':10,'deposits_current':20}.items()}
    return {'status':'facts_ready','period':'2026-06-30','facts':{k:{**v,'url':'https://example.org/report.pdf'} for k,v in facts.items()},'debt':50}


def policy():
    return {'operating_cash_reserve':10,'deposit_recovery':.5,'minority_multiple':2,'additional_claims':3,'share_dilution':2,
        'margin_required':.2,'basis':'合成手算验证；不是投资案例','valuation_date':'2026-10-25','cashflow_excludes_nonoperating':True}


def test_bridge_independent_hand_calculation_and_dated_price():
    dcf={'scenarios':{'基准':{'enterprise_value':180}}}
    out=bridge_scenarios(dcf,packet(),policy(),{'price':10,'as_of':'2026-10-23','evidence_id':'S2'})
    r=out['rows'][0]
    # (180 - 50 + (30-10) + 20*.5 - 5*2 - 3) / (10+2)
    assert r['equity_value']==147 and r['value_per_share']==12.25
    assert r['margin_of_safety']==pytest.approx(1-10/12.25)
    assert r['entry_price_at_required_margin']==pytest.approx(9.8)
    assert bridge_scenarios(dcf,packet(),policy(),{'price':10})['rows'][0]['margin_of_safety'] is None
    excluded=bridge_scenarios(dcf,packet(),dict(policy(),deposit_categories=[]))
    assert excluded['adjustments']['nonoperating_deposits']==0
    assert excluded['rows'][0]['equity_value']==137
    with pytest.raises(DataError):bridge_scenarios(dcf,packet(),dict(policy(),deposit_categories=['deposits_long']))
    with pytest.raises(DataError):bridge_scenarios(dcf,packet(),dict(policy(),cashflow_excludes_nonoperating=False))
    with pytest.raises(DataError):bridge_scenarios(dcf,packet(),dict(policy(),valuation_date='2026-01-01'))
    with pytest.raises(DataError):bridge_scenarios(dcf,packet(),dict(policy(),operating_cash_reserve=100))
    missing=dict(packet(),status='incomplete',missing=['shares'])
    assert bridge_scenarios(dcf,missing,policy())['rows']==[]


def test_intelligence_rejects_missing_chinese_and_fabricated_sources():
    a=[{'id':'A1','url':'https://example.org/news','title':'Official release'}]
    b={'title':'产品发布研究','summary':'公司发布新品，交付情况待确认。','facts':[{'text':'公司宣布发布新品。','refs':['A1']}],
       'impacts':[],'conflicts':[],'watch_next':[],'unknowns':['交付数据尚待确认']}
    assert validate_brief(b,a)==b
    assert '[Official release](https://example.org/news)' in brief_markdown(b,a)
    with pytest.raises(ValueError):validate_brief(dict(b,summary='English only'),a)
    bad=deepcopy(b);bad['facts'][0]['refs']=['invented']
    with pytest.raises(ValueError):validate_brief(bad,a)


def test_required_stages_execute_once_and_preserve_failures():
    from investment_agent.research_completion import required_company_steps
    class FakeAgent:
        question='深度价值投资研究'
        def __init__(self):
            self.coverage=CoverageChecklist();self.trace=[{'id':'S1','tool':'financial_model','arguments':{'code':'600001','name':'测试公司'},'result':{'ok':True,'data':{'analysis':{'rows':[{}]}}}}]
            self.calls=[]
        async def dispatch(self,name,args):
            self.calls.append((name,args))
            return {'ok':False,'error':{'code':'NETWORK_DOWN'}}
    a=FakeAgent()
    asyncio.run(required_company_steps(a))
    count=len(a.calls)
    assert count==5
    asyncio.run(required_company_steps(a))
    assert len(a.calls)==count and a.coverage.items['governance']['status']=='missing'


def test_resume_final_draft_does_not_repeat_tools_or_planning():
    from investment_agent.research_runtime import run_graph
    class Agent:
        trace=[]
    state={'trace':[{'id':'S1'}],'report':'已生成的待复核正文','calls':[]}
    result=asyncio.run(run_graph(Agent(),[],initial=state))
    assert result is state


def test_valuation_accepts_equivalent_ticker_but_rejects_other_security():
    from investment_agent.research_completion import same_company_arguments
    company={'code':'600001','name':'测试公司'}
    assert same_company_arguments({'code':'600001.SH','name':'公司全称','assumptions':{}},company)['code']=='600001'
    with pytest.raises(DataError):same_company_arguments({'code':'600002.SH'},company)


def test_required_valuation_accepts_financial_skill_envelope(tmp_path):
    import json
    from investment_agent.agent import ResearchAgent
    from investment_agent.config import Settings
    from investment_agent.research_completion import required_valuation
    assumptions={'revenue_growth':.02,'operating_margin':.1,'tax_rate':.2,'depreciation_ratio':.03,
        'capex_ratio':.04,'working_capital_ratio':.05,'wacc':.09,'terminal_growth':.02,'basis':'显式测试假设'}
    class Client:
        async def complete(self,messages,allow_tools):
            assert '2025-12-31' in messages[-1]['content']
            return {'content':json.dumps({'arguments':{'code':'600001.SH','name':'公司全称','assumptions':assumptions,
                'bridge_policy':dict(policy(),deposit_categories=[])}})},{}
    a=ResearchAgent(Settings(root=tmp_path),client=Client());a.question='价值投资估值'
    a.trace=[{'id':'S1','tool':'execute_professional_skill','arguments':{'name':'financial-quality'},
        'result':{'ok':True,'data':{'skill':'financial-quality','data':{'analysis':{'rows':[{'period':'2025-12-31','values':{'revenue':100}}]}}}}}]
    calls=[]
    async def dispatch(name,args):
        calls.append((name,args));return {'ok':True,'data':{}}
    a.dispatch=dispatch
    out=asyncio.run(required_valuation(a,{'code':'600001','name':'测试公司'},None))
    assert out[0]['result']['ok'] and calls[0][0]=='financial_model'
    assert calls[0][1]['code']=='600001' and calls[0][1]['name']=='测试公司'


def test_editor_corrections_are_atomic_and_evidence_bound():
    import json
    from investment_agent.report_review import apply_review
    trace=[{'id':'S1','result':{'ok':True}}]
    report='经营现金流为100元。尚待确认后续增长。'
    value={'summary':'已检查数值和来源。','reviewed_evidence':['S1'],
        'corrections':[{'original':'经营现金流为100元。','replacement':'经营现金流为120元。[S1]','reason':'与给定期间字段一致。','evidence_ids':['S1']}]}
    out,review=apply_review(report,json.dumps(value),trace)
    assert out=='经营现金流为120元。[S1]尚待确认后续增长。'
    bad=deepcopy(value);bad['corrections'][0]['original']='不存在的原文'
    with pytest.raises(DataError):apply_review(report,json.dumps(bad),trace)
    bad=deepcopy(value);bad['corrections'][0]['replacement']='新数字[S9]'
    with pytest.raises(DataError):apply_review(report,json.dumps(bad),trace)
    bad=deepcopy(value);bad['corrections'][0]['replacement']='[未经核验](https://invented.example/news)'
    with pytest.raises(DataError):apply_review(report,json.dumps(bad),trace)


def test_review_blocks_preserve_report_and_reject_excess():
    from investment_agent.report_review import review_blocks
    text='# 标题\n\n第一段。\n|标题|金额|\n|---|---|\n|收入|100|\n'
    blocks=review_blocks(text,20)
    assert ''.join(blocks)==text and all(len(b)<=20 for b in blocks)
    with pytest.raises(DataError):review_blocks('单行过长不可拆',3)
    with pytest.raises(DataError):review_blocks('abcd\n'*6,5)


def test_intelligence_pipeline_keeps_evidence_when_live_fetch_fails(tmp_path,monkeypatch):
    import json
    from investment_agent.config import Settings
    from investment_agent.intelligence.research_agent import IntelligenceResearchAgent
    class Sources:
        def search_intelligence(self,query):
            return {'ok':True,'source':'local_intelligence','data':[{'id':'E1','published':'2026-10-20'}]}
        def get_intelligence_event(self,eid):
            return {'ok':True,'data':{'articles':[{'id':'A1','title':'Official release','url':'https://example.org/release',
                'body':'公司表示产品进入客户测试，尚未公布收入。','coverage':'正文已提取','published':'2026-10-20','tier':'原始发布'}]}}
    value={'title':'商业化进展研究','summary':'目前只有测试阶段的企业自述。','facts':[{'text':'企业表示产品进入客户测试。','refs':['A1']}],
        'impacts':[],'conflicts':[],'watch_next':[{'text':'继续观察实际交付与收入。','refs':['A1']}],'unknowns':['独立交付验证尚未取得']}
    class Client:
        def __init__(self):self.calls=0
        async def complete(self,messages,allow_tools):
            self.calls+=1
            return {'content':json.dumps({'queries':['robot'],'questions':['核对交付情况']} if self.calls==1 else value,ensure_ascii=False)},{}
    def down(*args,**kw):raise ConnectionError('offline')
    monkeypatch.setattr('investment_agent.intelligence.collect.Fetcher.get',down)
    agent=IntelligenceResearchAgent(Settings(root=tmp_path),client=Client(),service=Sources())
    result=asyncio.run(agent.ask('机器人商业化进展'))
    assert result['status']=='completed' and agent.client.calls==3
    assert 'https://example.org/release' in result['report'] and Path(result['report_file']).is_file()
    assert any('ConnectionError' in a.get('retrieval','') for a in result['trace'][-1]['result']['data']['articles'])
    assert len(result['research_process'])==6


def test_technical_skill_uses_completed_daily_cutoff(tmp_path,monkeypatch):
    from investment_agent.agent import ResearchAgent
    from investment_agent.config import Settings
    from investment_agent.professional_skills import execute
    calls=[]
    async def collect(service,codes,start,end):
        calls.append((start,end));return {'ok':False,'error':{'code':'TEST_STOP'}}
    monkeypatch.setattr('investment_agent.quant_data.collect_history',collect)
    monkeypatch.setattr('investment_agent.professional_skills.now',lambda:'2026-10-26T15:30:00+08:00')
    asyncio.run(execute(ResearchAgent(Settings(root=tmp_path)),'technical-analysis',{'code':'600001.SH'}))
    assert calls[-1]==('2025-10-25','2026-10-25')
    monkeypatch.setattr('investment_agent.professional_skills.now',lambda:'2026-10-26T16:01:00+08:00')
    asyncio.run(execute(ResearchAgent(Settings(root=tmp_path)),'technical-analysis',{'code':'600001.SH'}))
    assert calls[-1]==('2025-10-26','2026-10-26')


def test_bridge_tables_end_before_assumption_paragraphs():
    from investment_agent.capital_bridge import bridge_markdown
    from markdown_it import MarkdownIt
    facts=packet()
    for key,v in facts['facts'].items():v.update(label=key,unit='元',page=1)
    value=bridge_scenarios({'scenarios':{'基准':{'enterprise_value':180}}},facts,policy(),{'price':10,'as_of':'2026-10-23','evidence_id':'S2'})
    tokens=MarkdownIt('commonmark').enable('table').parse(bridge_markdown(facts,value))
    assert sum(t.type=='table_open' for t in tokens)==2
    in_table=False
    for token in tokens:
        if token.type=='table_open':in_table=True
        if token.type=='table_close':in_table=False
        if token.type=='inline' and '桥接假设依据' in token.content:assert not in_table
