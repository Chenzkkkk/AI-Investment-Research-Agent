"""Regressions discovered by the live consumer-company web acceptance run."""
import asyncio
from pathlib import Path
from io import BytesIO
import json
import pytest
from pypdf import PdfReader
from investment_agent.sector_screen import normalize_financials
from investment_agent.report_data import projections
from investment_agent.report_pdf import build_pdf
from investment_agent.valuation_views import scenario_frame,valuation_markdown
from investment_agent.financial_model import dcf_scenarios
from investment_agent.research_runtime import ResearchBudget,ResearchStore,ACTIVE_BUDGET,run_graph,synthesis_messages
from investment_agent.agent import ResearchAgent
from investment_agent.config import Settings


def test_model_view_keeps_latest_quotes_and_explains_column_presence():
    from investment_agent.research_runtime import model_view
    data={'rows':[{'date':f'2026-10-{i:02}','close':i} for i in range(1,24)],
          'flags':{'tradable':False},'coverage_by_symbol':{}}
    view=model_view(data)
    assert view['rows'][0]['date']=='2026-10-23'
    assert 'flags' not in view and view['field_availability']['tradable'] is False
    assert '不是停牌' in view['field_availability_note']


def test_operating_revenue_is_not_total_operating_income():
    records=[{'period':'2025-12-31','published_at':'2026-04-30','report':'income','url':'https://example.com/filing',
              'row':{'OPERATE_INCOME':100,'TOTAL_OPERATE_INCOME':103}}]
    asset={'code':'600887.SH','name':'消费公司'}
    data=normalize_financials(records,asset,'2026-06-30')['periods'][0]['values']
    assert data['revenue']==100 and data['total_operating_income']==103
    records[0]['row'].pop('OPERATE_INCOME')
    data=normalize_financials(records,asset,'2026-06-30')['periods'][0]['values']
    assert data.get('revenue') is None and data['total_operating_income']==103


def test_statement_fallback_filters_identity_and_publication():
    from investment_agent.public_statement_details import supplement_revenue
    from investment_agent.common import Result
    good={'REPORT_DATE':'2025-12-31','NOTICE_DATE':'2026-04-30','SECURITY_CODE':'600887',
          'SECUCODE':'600887.SH','OPERATE_INCOME':100,'TOTAL_OPERATE_INCOME':103}
    class CachedService:
        def __init__(self): self.calls=[]
        async def fetch(self,source,params,loader,ttl):
            self.calls.append(params)
            if 'statement_category' in params:
                return Result(source='eastmoney_public',kind='category',data={'company_type':'4'})
            rows=[good,dict(good,SECUCODE='600000.SH'),dict(good,NOTICE_DATE='2026-07-01'),dict(good,OPERATE_INCOME=None)]
            return Result(source='eastmoney_public',kind='income',data={'rows':rows},metadata={'url':'https://emweb.securities.eastmoney.com/statement'})
    service=CachedService()
    records=[{'period':'2025-12-31','published_at':'2026-04-30','row':{}}]
    rows,requests=asyncio.run(supplement_revenue(service,records,{'code':'600887.SH'},'2026-06-30'))
    assert len(rows)==1 and rows[0]['row']['OPERATE_INCOME']==100 and len(service.calls)==2
    assert all(r['ok'] for r in requests)
    rows,requests=asyncio.run(supplement_revenue(service,[dict(records[0],row={'OPERATE_INCOME':100})],{'code':'600887.SH'},'2026-06-30'))
    assert rows==requests==[] and len(service.calls)==2


def test_sector_screen_can_use_explicit_total_income_without_aliasing():
    from investment_agent.sector_screen import rank_candidates
    asset={'code':'600887.SH','name':'消费公司','segment':'乳品','screen_row':{
        '最新价(元) 2026.10.23':28,'成交额(元) 2026.10.23':2e8,
        '市盈率(TTM) 2026.10.23':18,'近20日涨跌幅(%) 2026.10.23':10}}
    records=[]
    for year,income in [(2025,1000),(2026,1100)]:
        records.append({'period':f'{year}-06-30','published_at':f'{year}-08-28','report':'income','url':'https://example.com/filing',
            'row':{'TOTAL_OPERATE_INCOME':income,'PARENT_NETPROFIT':100,'DEDUCT_PARENT_NETPROFIT':90,
            'NETCASH_OPERATE':110,'TOTAL_ASSETS':2000,'TOTAL_LIABILITIES':1000,'ACCOUNTS_RECE':100,'INVENTORY':100,'WEIGHTAVG_ROE':10}})
    data=normalize_financials(records,asset,'2026-10-25')
    row=rank_candidates([asset],{asset['code']:data},'2026-10-25','value')[0]
    assert row['eligible'] and row['income_growth_yoy_pct']==pytest.approx(10)
    assert row['income_growth_basis']=='营业总收入' and row['revenue_yoy_pct'] is None


def test_dcf_enters_report_dashboard_and_pdf(tmp_path):
    assumptions={'revenue_growth':.02,'operating_margin':.12,'tax_rate':.20,'depreciation_ratio':.03,
        'capex_ratio':.04,'working_capital_ratio':.05,'wacc':.09,'terminal_growth':.02,'years':5,'basis':'测试情景，非公司指引'}
    dcf=dcf_scenarios(1e9,'2025-12-31',assumptions)
    result={'question':'消费公司研究','created_at':'2026-06-30','report':'# 消费公司研究\n\n估值依赖显式假设。',
        'trace':[{'id':'S1','tool':'execute_professional_skill','result':{'data':{'skill':'valuation','data':dcf,'assumptions':{'inputs':assumptions}}}}]}
    projection=projections(result,tmp_path);item=projection['valuations'][0]
    frame=scenario_frame(item)
    assert len(frame)==3 and frame.iloc[1]['经营企业价值（亿元）']==pytest.approx(dcf['scenarios']['基准']['enterprise_value']/1e8)
    assert '不能直接当成每股价格' in valuation_markdown(item)
    text=''.join(p.extract_text() for p in PdfReader(BytesIO(build_pdf(result,tmp_path))).pages)
    for needle in ('估值模型、情景与假设','敏感性','测试情景','WACC'): assert needle in text


def test_revaluation_does_not_duplicate_company_financial_charts(tmp_path):
    rows=[{'period':'2025-12-31','values':{'revenue':100}}]
    first={'name':'消费公司','as_of':'2026-06-30','analysis':{'rows':rows}}
    second={**first,'dcf':{'scenarios':{'基准':{'enterprise_value':1000}}}}
    result={'trace':[{'id':'S1','tool':'financial_model','result':{'data':first}},
                     {'id':'S2','tool':'financial_model','result':{'data':second}}]}
    data=projections(result,tmp_path)
    assert len(data['finances'])==len(data['valuations'])==1
    assert data['finances'][0]['dcf']==second['dcf']


def test_pending_input_does_not_consume_report_reserve(tmp_path):
    class Client:
        async def complete(self,messages,allow_tools=True):
            assert not allow_tools
            assert len(json.dumps(messages))<10000
            estimate,limit=ACTIVE_BUDGET.get().reserve(messages,1000)
            ACTIVE_BUDGET.get().record(estimate,{'prompt_tokens':1000,'completion_tokens':100})
            return {'content':'已根据现有证据生成阶段判断。'},{}
    agent=ResearchAgent(Settings(root=tmp_path),client=Client());agent.question='研究消费公司'
    trace=[{'id':'S1','tool':'stock_data','result':{'ok':True,'source':'public','data':{'price':20}}}]
    state={'messages':[{'role':'user','content':'x'*200000}],'trace':trace,'usage':[],'round':3,'report':'','calls':[],'failures':{}}
    b=ResearchBudget(input_limit=30000,input_tokens=12000)
    token=ACTIVE_BUDGET.set(b)
    try: r=asyncio.run(run_graph(agent,[],initial=state))
    finally:ACTIVE_BUDGET.reset(token)
    assert r['report'] and b.input_tokens==13000


def test_partial_resume_keeps_checkpoint_and_previous_report(tmp_path):
    store=ResearchStore(tmp_path);cid=store.create_conversation();tid=store.create_task(cid,'研究',{})
    store.claim(tid);store.checkpoint(tid,{'budget':{'input_tokens':12000}})
    store.finish(tid,{'report':'预算中断'},'partial')
    assert store.resume(tid) and json.loads(store.task(tid)['checkpoint'])['budget']['input_tokens']==12000
    store.claim(tid);store.finish(tid,{'report':'继续完成'})
    assert len(store.messages(cid))==3 and store.task(tid)['status']=='completed'
