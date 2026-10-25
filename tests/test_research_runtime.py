import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import json
import pytest
from investment_agent.common import DataError,Result
from investment_agent.config import Settings
from investment_agent.professional_skills import CoverageChecklist,catalog,method
from investment_agent.research_runtime import ResearchStore,ResearchBudget,run_graph
from investment_agent.agent import ResearchAgent
from investment_agent.service import DataService


def test_concurrent_claim_and_conversations(tmp_path):
    store=ResearchStore(tmp_path); cid=store.create_conversation(); tid=store.create_task(cid,'test',{})
    assert store.create_task(cid,'double click',{})==tid
    with ThreadPoolExecutor(max_workers=4) as pool: results=list(pool.map(lambda _:store.claim(tid),range(4)))
    assert sum(results)==1
    store.checkpoint(tid,{'round':2}); assert json.loads(store.task(tid)['checkpoint'])['round']==2
    store.finish(tid,{'report':'done'}); store.finish(tid,{'report':'duplicate'})
    assert len(store.messages(cid))==2


def test_coverage_cannot_be_satisfied_by_method_notes():
    coverage=CoverageChecklist()
    with pytest.raises(DataError): coverage.update([{'factor':'financial','status':'checked','claim':'ok','evidence_ids':[]}],[])
    trace=[{'id':'S1','result':{'ok':True,'source':'research_method','data':{}}}]
    with pytest.raises(DataError): coverage.update([{'factor':'financial','status':'checked','claim':'ok','evidence_ids':['S1']}],trace)
    trace[0]['result']['source']='public_statements'
    trace[0]['result']['data']={'cashflow':-5,'period':'2025-12-31'}
    result=coverage.update([{'factor':'financial','status':'checked','claim':'cashflow inspected','evidence_ids':['S1']}],trace)
    assert result['quality_gate']=='incomplete'
    assert result['important_unknowns']


def test_every_skill_is_packaged_with_a_method():
    for spec in catalog():
        assert method(spec['name'])['instructions'].startswith('---')
        assert spec['version'] and spec['checks']


def test_shared_budget_cap_and_cancellation():
    b=ResearchBudget(max_model_calls=1,input_limit=10000,output_limit=2000)
    estimate,limit=b.reserve([{'content':'abc'}],8000); assert limit==2000
    b.record(estimate,{'prompt_tokens':10,'completion_tokens':20})
    assert b.input_tokens==10 and b.output_tokens==20
    with pytest.raises(DataError): b.reserve([],100)
    with pytest.raises(DataError): ResearchBudget(cancelled=lambda:True).reserve([],100)


def test_graph_observes_failure_and_does_not_repeat_same_request(tmp_path):
    class Client:
        count=0
        async def complete(self,messages,allow_tools=True):
            self.count+=1
            if self.count<3:
                return {'role':'assistant','content':None,'tool_calls':[{'id':str(self.count),'type':'function','function':{'name':'stock_data','arguments':'{"code":"601138"}'}}]},{}
            return {'role':'assistant','content':'资料不足，停止重复查询。'},{}
    agent=ResearchAgent(Settings(root=tmp_path),client=Client())
    calls=[]
    async def dispatch(name,args): calls.append(name); return {'ok':False,'error':'offline'}
    agent.dispatch=dispatch
    result=asyncio.run(run_graph(agent,[{'role':'user','content':'test'}]))
    assert len(calls)==1 and len(result['trace'])==2 and '停止' in result['report']


def test_miaoxiang_quota_persists_across_tasks(tmp_path):
    settings=Settings(root=tmp_path,enable_miaoxiang=True)
    first=DataService(settings)
    async def fail(): raise DataError('MIAOXIANG_QUOTA','no quota')
    asyncio.run(first.fetch('miaoxiang',{'q':'x'},fail))
    async def forbidden(): raise AssertionError('must not hit network')
    second=DataService(settings)
    result=asyncio.run(second.fetch('miaoxiang',{'q':'new'},forbidden))
    assert result.error['code']=='SOURCE_CIRCUIT_OPEN' and second.calls==0


def test_upstream_roles_work_without_clone(tmp_path):
    from investment_agent.upstream_adapter import manifest,guidance,perspective
    assert len(manifest())==8 and 'core/' not in '\n'.join(x['source'] for x in manifest().values())
    assert guidance('fundamentals') and guidance('technicals')
    class Client:
        async def complete(self,messages,allow_tools=False): return {'content':'反证分析[S1]'},{}
    agent=ResearchAgent(Settings(root=tmp_path),client=Client())
    agent.trace=[{'id':'S1','result':{'ok':True,'data':{'price':10}},'tool':'stock_data'}]
    assert asyncio.run(perspective(agent,'bear',['S1'],'what risks'))['data']['upstream']['license']=='Apache-2.0'


def test_leaked_tool_serialization_is_never_a_report(tmp_path):
    class Client:
        count=0
        async def complete(self,messages,allow_tools=True):
            self.count+=1
            return {'role':'assistant','content':'<DSML calls>python secret</DSML>' if self.count==1 else '证据不足，保留未知。'},{}
    agent=ResearchAgent(Settings(root=tmp_path,max_rounds=2),client=Client())
    result=asyncio.run(run_graph(agent,[{'role':'user','content':'test'}]))
    assert 'DSML' not in result['report'] and agent.client.count==2


def test_rule_index_respects_notice_dates_and_exchange():
    from investment_agent.china_rules import applicable_rules
    before=applicable_rules('2026-04-01','SSE')['entries']
    assert not any(r['id']=='sse-trading-2026' for r in before)
    pending=applicable_rules('2026-05-01','SSE')['entries'][0]
    assert pending['applicability']=='not_effective_yet'
    assert applicable_rules('2026-10-25','SSE')['entries'][0]['applicability']=='effective_notice'
    assert not any(r['id']=='sse-trading-2026' for r in applicable_rules('2026-10-25','SZSE')['entries'])


def test_financial_model_context_has_numbers_and_bounded_sources():
    from investment_agent.research_runtime import model_view
    data={'symbol':'601138.SH','name':'test','as_of':'2026-10-25',
          'analysis':{'rows':[{'period':'2025-12-31','values':{'revenue':100},'sources':[{'url':'x'*10000}]}]},
          'document_analysis':[{'notes':[{'factor':'customer_concentration','excerpt':'客户集中62%','url':'https://example.com'}]}]}
    view=model_view(data)
    assert view['rows'][0]['values']['revenue']==100
    assert view['document_checks'][0]['notes'][0]['excerpt']=='客户集中62%'
    assert len(json.dumps(view))<6000


def test_news_falls_back_without_paid_credentials(tmp_path,monkeypatch):
    from investment_agent import china_research
    from investment_agent.ashare_sources import news_records
    async def discover(*args,**kwargs):
        return {'evidence':[{'title':'机器人公司产品发布公告','url':'https://www.cninfo.com.cn/test',
                'published_at':'2026-01-01','body':'机器人产品发布，订单情况尚待核查。'}],'requests':[]}
    monkeypatch.setattr(china_research,'discover_official',discover)
    service=DataService(Settings(root=tmp_path,enable_miaoxiang=False,enable_public=True))
    response=asyncio.run(service.news('机器人',code='601138.SH',name='工业富联'))
    assert response.ok and response.source=='public_news'
    records=news_records(response.data,'2026-10-25',['机器人'])
    assert len(records['accepted'])==1 and records['accepted'][0]['origin']=='public_discovery'
