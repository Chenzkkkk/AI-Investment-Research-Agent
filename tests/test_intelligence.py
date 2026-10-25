import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime,timedelta
import json
from pathlib import Path
import socket

import pytest
from investment_agent.config import Settings
from investment_agent.intelligence.store import Store,DOMAINS,TZ,now,digest,dumps
from investment_agent.intelligence.collect import canonical,public_addresses,FetchError,date,discover,collect
from investment_agent.intelligence.analyze import validate_analysis,analyze_one
from investment_agent.intelligence.service import IntelligenceService
from investment_agent.intelligence.worker import single_worker


def article(**kw):
    return dict(source_id='openai',url='https://example.com/a',title='产品发布',published=now(),body='这是来源中的真实原话。'*30,
                excerpt='',domain=DOMAINS[3],region='全球',tier='原始发布',coverage='正文已提取',**kw)


def analysis(aid,**kw):
    value=dict(title_zh='测试产品发布',summary_zh='来源宣布发布产品。',domain=DOMAINS[3],event_type='发布',topics=['机器人'],
               facts=[dict(text='来源宣布发布产品。',refs=[aid])],entities=[],statements=[],impacts=[],unknowns=['交付情况未知'],watch_next=[],conflicts=[],duplicate_of='',merge_reason='')
    value.update(kw);return value


@pytest.fixture
def store(tmp_path): return Store(tmp_path)


def test_exact_reprint_and_revisions(store):
    a=article();eid,changed=store.ingest(a)
    assert changed
    assert store.ingest(a)==(eid,False)
    a['url']='https://publisher.example/b';a['tier']='媒体报道'
    assert store.ingest(a)[0]==eid
    a['body']='更正后的原始材料。'*40
    assert store.ingest(a)==(eid,True)
    e=store.event(eid)
    assert len(e['articles'])==2 and len(e['revisions'])==3


def test_different_events_not_merged_by_title(store):
    a=article();first=store.ingest(a)[0]
    a['url']='https://example.com/b';a['body']='同主题不同版本的新产品'*50
    assert store.ingest(a)[0]!=first


def test_historical_revision_does_not_leak(store,monkeypatch):
    import investment_agent.intelligence.store as module
    monkeypatch.setattr(module,'now',lambda:'2026-10-20T12:00:00+08:00')
    a=article();a['published']='2026-10-19T12:00:00+08:00'
    eid=store.ingest(a)[0]
    monkeypatch.setattr(module,'now',lambda:'2026-10-22T12:00:00+08:00')
    a['body']='尚不可见的更正信息'*40;store.ingest(a)
    before=store.event(eid,'2026-10-21T12:00:00+08:00')
    assert '更正' not in before['articles'][0]['body']
    assert store.event(eid,'2026-10-18T12:00:00+08:00') is None


def test_unknown_time_excluded_from_historical_answer(store):
    a=article();a['published']=None;eid=store.ingest(a)[0]
    assert store.event(eid,now()) is None
    assert store.search()[0]['published'] is None


def test_read_marks_persistent_and_follow(store):
    eid=store.ingest(article())[0]
    store.mark(eid,'star');store.mark(eid,'later');store.mark(eid,'read_at');store.follow('机器人')
    another=Store(store.directory.parent.parent)
    assert another.event(eid)['marks']['star']==1
    assert another.follows()==['机器人']
    another.follow('机器人',True);assert not another.follows()


def test_search_unicode_and_literal_sql(store):
    store.ingest(article())
    assert len(store.search('产品'))==1
    assert not store.search("%' OR 1=1 --")
    assert not store.search('%')


def test_budget_atomic_and_persistent(store):
    store.configure({'daily_events':3})
    with ThreadPoolExecutor(max_workers=6) as executor:
        reservations=list(executor.map(lambda _:store.reserve(100,100),range(12)))
    assert sum(r is not None for r in reservations)==3
    assert store.usage()['events']==3
    store.settle(next(r for r in reservations if r),{'prompt_tokens':50,'completion_tokens':70})
    assert store.usage()['input']==250 and store.usage()['output']==270


@pytest.mark.parametrize('url',['http://user:pass@example.com','ftp://example.com','https://example.com:9999','https://example.com/?token=secret'])
def test_unsafe_url_rejected(url):
    with pytest.raises(FetchError):canonical(url)


@pytest.mark.parametrize('ip',['127.0.0.1','192.168.1.4','169.254.169.254','::1','fc00::1'])
def test_private_dns_rejected(monkeypatch,ip):
    monkeypatch.setattr(socket,'getaddrinfo',lambda *a,**k:[(2,1,6,'',(ip,443))])
    with pytest.raises(FetchError): public_addresses('public-looking.example',443)


def test_canonical_tracking_and_time():
    assert canonical('https://example.com/news?utm_source=x&id=12#anchor')=='https://example.com/news?id=12'
    assert date('not-a-date') is None
    assert date('2999-01-01') is None
    assert date('2026-10-20')=='2026-10-20T00:00:00+08:00'


def test_quote_and_reference_validation():
    a=article();a['id']='a1';v=analysis('a1')
    v['statements']=[dict(speaker='某人',identity='机构甲',context='访谈',quote='这是来源中的真实原话。',interpretation_zh='原话摘录',ref='a1')]
    validate_analysis(v,[a],set())
    v['statements'][0]['quote']='模型编造的原话'
    with pytest.raises(ValueError):validate_analysis(v,[a],set())
    v=analysis('missing')
    with pytest.raises(ValueError):validate_analysis(v,[a],set())


def test_ambiguous_merge_target_rejected():
    a=article();a['id']='a1'
    with pytest.raises(ValueError):validate_analysis(analysis('a1',duplicate_of='unknown',merge_reason='x'),[a],set())


def test_crosslanguage_merge_retains_provenance(store):
    a=article();first=store.ingest(a)[0]
    a['url']='https://example.com/english';a['title']='A product launch';a['body']='The same launch was reported in English. '*30
    second=store.ingest(a)[0]
    aid=store.event(second)['articles'][0]['id']
    class Fake:
        async def complete(self,*args,**kwargs):
            return {'content':dumps(analysis(aid,duplicate_of=first,merge_reason='同一产品同次发布的中英文报道'))},{'prompt_tokens':100,'completion_tokens':100}
    assert asyncio.run(analyze_one(store,Settings(root=store.directory.parent.parent),second,Fake()))=='analyzed'
    assert len(store.event(second)['articles'])==2
    assert len(store.search())==1


def test_same_name_entities_keep_identity(store):
    eid=store.ingest(article())[0];aid=store.event(eid)['articles'][0]['id']
    value=analysis(aid,entities=[dict(kind='人物',name='李明',identity='机构甲研究员'),dict(kind='人物',name='李明',identity='机构乙负责人')])
    store.save_analysis(eid,value,'hash')
    with store.db() as db:assert db.execute('SELECT count(*) FROM entities').fetchone()[0]==2


def test_conflicting_claims_are_preserved(store):
    eid=store.ingest(article())[0];aid=store.event(eid)['articles'][0]['id']
    value=analysis(aid,conflicts=[dict(text='发布口径和后续核实不一致，待确认',refs=[aid])])
    store.save_analysis(eid,value,'hash');assert store.event(eid)['analysis']['conflicts']


def test_html_source_failure_does_not_erase_history(store,monkeypatch):
    import investment_agent.intelligence.collect as module
    eid=store.ingest(article())[0]
    class Failed:
        def get(self,*a,**k):raise FetchError('HTTP 429')
        def close(self):pass
    monkeypatch.setattr(module,'Fetcher',Failed)
    result=collect(store,force=True,source_ids=['openai'])
    assert result['sources'][0]['error']=='HTTP 429'
    assert store.event(eid)


def test_zero_feed_is_success_not_failure():
    class Empty:
        def get(self,*a,**k):return b'<rss version="2.0"><channel><title>Example</title></channel></rss>',{},'https://example.com',200
    assert discover(Empty(),{'kind':'rss','url':'https://example.com'}, {})[0]==[]


def test_read_apis_no_model_or_network(store,monkeypatch):
    from investment_agent.agent import DeepSeekClient
    monkeypatch.setattr(DeepSeekClient,'complete',lambda *a,**k:pytest.fail('read must not call model'))
    store.ingest(article())
    service=IntelligenceService(store.directory.parent.parent)
    assert service.search_intelligence('产品')['data']
    assert service.get_topic_brief('产品',7)['data']


def test_worker_lock(store):
    with single_worker(store.directory) as first:
        assert first
        with single_worker(store.directory) as second: assert not second


def test_config_rejects_invalid(store):
    with pytest.raises(ValueError):store.configure({'max_documents':10000})
    with pytest.raises(ValueError):store.configure({'reasoning_effort':'secret'})


def test_pending_discovery_survives_304(store,monkeypatch):
    import investment_agent.intelligence.collect as module
    stamp=now(); source=next(r for r in store.sources() if r['id']=='openai')
    existing=article();existing['url']='https://example.com/old';store.ingest(existing)
    queued=dict(url='https://example.com/new',title='New public report',published=stamp,excerpt='original summary',author='')
    with store.db() as db:db.execute('INSERT INTO crawl_queue VALUES(?,?,?,?,0)',('openai',queued['url'],dumps(queued),stamp))
    class Cached:
        def get(self,url,headers=None):
            if 'rss.xml' in url:return b'',{},url,304
            return b'<html><body>report</body></html>',{},url,200
        def close(self):pass
    monkeypatch.setattr(module,'Fetcher',Cached)
    monkeypatch.setattr(module,'extract_document',lambda *a:dict(body='New original report '*30,published=stamp,author='',title='New public report'))
    result=collect(store,True,['openai'])
    assert result['new_or_updated']==1
    assert store.search('New public report')


def test_read_tools_integrated_with_research_agent(store):
    from investment_agent.agent import ResearchAgent,TOOLS
    eid=store.ingest(article())[0]
    cfg=Settings(root=store.directory.parent.parent,enable_choice=False,enable_miaoxiang=False)
    agent=ResearchAgent(cfg)
    assert asyncio.run(agent.dispatch('search_intelligence',{'query':'产品'}))['data']
    assert asyncio.run(agent.dispatch('get_intelligence_event',{'event_id':eid}))['data']['id']==eid
    assert asyncio.run(agent.dispatch('get_topic_brief',{'topic':'产品','period':7}))['data']


def test_future_cutoff_rejected(store):
    service=IntelligenceService(store.directory.parent.parent)
    with pytest.raises(ValueError):service.search_intelligence(as_of='2999-01-01')


def test_budget_exhaustion_keeps_raw_evidence(store):
    eid=store.ingest(article())[0];store.configure({'daily_events':0})
    class Never:
        async def complete(self,*a,**k):pytest.fail('exhausted budget must not call model')
    assert asyncio.run(analyze_one(store,Settings(),eid,Never()))=='budget_exhausted'
    assert store.event(eid)['articles'] and not store.event(eid)['analysis']


def test_metadata_correction_versioned(store):
    a=article();eid=store.ingest(a)[0]
    a['title']='更正后的标题，正文未变'
    assert store.ingest(a)==(eid,True)
    assert len(store.event(eid)['revisions'])==2


def test_historical_filters_use_past_classification(store,monkeypatch):
    import investment_agent.intelligence.store as module
    monkeypatch.setattr(module,'now',lambda:'2026-10-20T12:00:00+08:00')
    a=article();a['published']='2026-10-19T12:00:00+08:00';eid=store.ingest(a)[0]
    aid=store.event(eid)['articles'][0]['id']
    store.save_analysis(eid,analysis(aid),'first')
    monkeypatch.setattr(module,'now',lambda:'2026-10-22T12:00:00+08:00')
    store.save_analysis(eid,analysis(aid,domain=DOMAINS[0]),'second')
    assert store.search(filters={'domain':DOMAINS[3]},as_of='2026-10-21T00:00:00+08:00')
    assert not store.search(filters={'domain':DOMAINS[0]},as_of='2026-10-21T00:00:00+08:00')
