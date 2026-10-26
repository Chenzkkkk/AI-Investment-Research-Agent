import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
from pathlib import Path
import pytest
from investment_agent.config import Settings
from investment_agent.intelligence.store import Store,now,DOMAINS,dumps
from investment_agent.intelligence.materials import catalog,material,queue_brief,run_brief,brief_record
from investment_agent.intelligence.collect import catalog_discoveries


def put(store,index=0):
    store.ingest(dict(source_id='openai',url=f'https://example.org/{index}',title=f'Original material {index}',published=now(),
        body=f'Product {index} supports local deployments. Details have not been independently tested.',excerpt='',domain=DOMAINS[3],region='全球',tier='原始发布',coverage='正文已提取'))


def test_every_material_is_reachable_beyond_old_limits(tmp_path):
    s=Store(tmp_path)
    for i in range(71):put(s,i)
    pages=[catalog(s,page=p) for p in range(1,7)]
    assert pages[0]['count']==71 and len({a['id'] for p in pages for a in p['rows']})==71
    assert catalog(s,"%' OR 1=1 --")['count']==0
    assert catalog(s,filters={'source':'nasa'})['count']==0


def test_raw_queue_is_visible_before_body_budget(tmp_path):
    s=Store(tmp_path)
    with s.db() as db:
        for i in range(10):db.execute('INSERT INTO crawl_queue VALUES(?,?,?,?,0)',('openai',f'https://example.org/{i}',dumps(dict(url=f'https://example.org/{i}',title=f'Publication {i}',published=None,excerpt='source summary',author='')),now()))
    assert catalog_discoveries(s)==10 and catalog(s)['count']==10
    assert catalog_discoveries(s)==0
    assert all(a['coverage'].startswith('待补正文') for a in catalog(s)['rows'])


def test_user_selected_brief_is_durable_separate_from_auto_budget(tmp_path):
    s=Store(tmp_path);put(s);s.configure({'daily_events':0,'model_enabled':False})
    a=catalog(s)['rows'][0]
    with ThreadPoolExecutor(max_workers=2) as pool:claimed=list(pool.map(lambda _:queue_brief(s,a['id']),range(2)))
    assert sum(claimed)==1
    class Model:
        async def complete(self,messages,allow_tools):
            assert 'Product 0' in messages[1]['content'] and not allow_tools
            return {'content':json.dumps(dict(title_zh='产品部署材料',summary_zh='来源称产品支持本地部署。',facts=['来源介绍本地部署能力。'],implications=[],unknowns=['尚无独立测试。']),ensure_ascii=False)},{'prompt_tokens':100,'completion_tokens':200}
    assert asyncio.run(run_brief(s,Settings(root=tmp_path),a['id'],Model(),fetch=False))=='completed'
    assert not queue_brief(s,a['id'])
    assert catalog(Store(tmp_path),filters={'brief':'ready'})['count']==1
    assert catalog(s,filters={'brief':'pending'})['count']==0
    assert brief_record(s,a['id'])['brief']['url']==a['url']
    assert s.usage()['events']==0
    with s.db() as db:db.execute("UPDATE articles SET content_hash='changed' WHERE id=?",(a['id'],))
    assert catalog(s,filters={'brief':'ready'})['count']==0 and queue_brief(s,a['id'])


def test_failed_brief_keeps_material_and_needs_explicit_retry(tmp_path):
    s=Store(tmp_path);put(s);a=catalog(s)['rows'][0];queue_brief(s,a['id'])
    class Bad:
        async def complete(self,*args,**kw):raise ConnectionError('no network')
    assert asyncio.run(run_brief(s,Settings(root=tmp_path),a['id'],Bad(),fetch=False))=='failed'
    assert material(s,a['id'])['body'] and brief_record(s,a['id'])['error']=='ConnectionError'
    assert asyncio.run(run_brief(s,Settings(root=tmp_path),a['id'],Bad(),fetch=False))=='not_queued'
    assert queue_brief(s,a['id'])
