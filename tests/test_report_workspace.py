"""Report persistence, evidence safety and actual PDF rendering contracts."""
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
import json
import pandas as pd
from pypdf import PdfReader
from streamlit.testing.v1 import AppTest
from investment_agent.report_data import catalog,projections,source_links
from investment_agent.report_pdf import build_pdf
from investment_agent.research_runtime import ResearchStore
from investment_agent.config import Settings

ROOT=Path(__file__).resolve().parents[1]
RID='abcdef0123456789abcdef0123456789'


def result():
    return {'question':'样例公司的现金流质量如何？','created_at':'2025-06-30T18:00:00+08:00','model':'test',
        'report_file':RID+'.md','report':'## 主要判断\n\n经营现金流需要结合资本开支核验。\n\n[公告原文](https://www.cninfo.com.cn/)\n\n| 指标 | 说明 |\n|---|---|\n'+''.join('|现金流|需要核验附注与合并范围|\n' for _ in range(80)),
        'research_coverage':{'items':[{'label':'现金流','status':'missing','watch_next':'核查投资支出'}]},
        'trace':[{'id':'S1','tool':'financial_model','result':{'data':{'name':'样例公司','analysis':{'rows':[
            {'period':'2023-12-31','values':{'revenue':1e9,'operating_cashflow':2e8,'profit_parent':None}},
            {'period':'2024-12-31','values':{'revenue':1.1e9,'operating_cashflow':-1e8,'profit_parent':3e8}},
            {'period':'2025-06-30','values':{'revenue':8e8}}]}}}}]}


def test_pdf_chinese_tables_links_and_vector_candles(tmp_path):
    r=result(); data=projections(r,tmp_path)
    data['candles']=[{'label':'样例公司','frame':pd.DataFrame([
        {'date':'2025-06-26','open':10,'close':11,'high':12,'low':9},
        {'date':'2025-06-27','open':11,'close':10,'high':13,'low':9}]),
        'meta':{'source':'test fixture','adjustment':'raw','end':'2025-06-27'}}]
    pdf=build_pdf(r,tmp_path,visual_data=data)
    reader=PdfReader(BytesIO(pdf)); text=''.join(p.extract_text() for p in reader.pages)
    assert len(reader.pages)>=4
    for expected in ('经营现金流','年度财务','日 K 线','研究覆盖','核查投资支出','资料与原文索引'):
        assert expected in text
    links=[a.get_object().get('/A',{}).get('/URI') for p in reader.pages for a in p.get('/Annots',[])]
    assert 'https://www.cninfo.com.cn/' in links
    # No remote image fetch or executable HTML in exported Markdown.
    malicious={**r,'report':'<script>alert(1)</script>\n\n![x](http://127.0.0.1/private)\n\n[bad](javascript:alert)'}
    assert build_pdf(malicious,tmp_path).startswith(b'%PDF')


def test_library_and_favorites_survive_reopen(tmp_path):
    p=tmp_path/'data/reports';p.mkdir(parents=True)
    (p/(RID+'.json')).write_text(json.dumps(result()),encoding='utf-8')
    (p/'diagnostic.json').write_text(json.dumps({'report':'ignore'}))
    assert len(catalog(tmp_path))==1
    store=ResearchStore(tmp_path);store.set_setting('favorite_reports',['report:'+RID])
    assert ResearchStore(tmp_path).setting('favorite_reports')==['report:'+RID]
    app=AppTest.from_file(str(ROOT/'app.py'))
    with patch('investment_agent.config.Settings.load',return_value=Settings(root=tmp_path)):
        app.run(timeout=30)
        assert app.radio[0].value=='产品首页'
        assert all('AI · 机器人 · 前沿科技' not in x.value and '不止得到一个答案' not in x.value for x in app.markdown)
        app.radio[0].set_value('报告库').run(timeout=30)
        assert not app.exception and any(b.label=='★ 已收藏' for b in app.button)
        app.query_params['report']=RID;app.run(timeout=30)
        assert not app.exception and len(app.tabs)==4 and len(app.get('plotly_chart'))==1
        next(b for b in app.button if b.label=='生成 PDF 报告').click().run(timeout=30)
        assert not app.exception and list((p/'exports').glob('*.pdf'))


def test_invalid_report_path_and_missing_snapshot(tmp_path):
    r=result();r['trace'].append({'id':'S2','tool':'execute_professional_skill','result':{'data':{
        'skill':'technical-analysis','data':{'snapshot':{'data':{'id':'missing'}}}}}})
    assert projections(r,tmp_path)['warnings']
    assert source_links(r)==[{'title':'公告原文','url':'https://www.cninfo.com.cn/'}]
    with patch('investment_agent.config.Settings.load',return_value=Settings(root=tmp_path)):
        app=AppTest.from_file(str(ROOT/'app.py'));app.query_params['report']='../../secret';app.run(timeout=20)
        assert app.error and not app.exception


def test_snapshot_cutoff_and_synthetic_rejection(tmp_path):
    from investment_agent.quant_data import DatasetStore
    frame=pd.DataFrame([{'date':date,'symbol':'601138.SH','open':10,'high':12,'low':9,'close':11,'volume':1000}
        for date in ['2025-06-26','2025-06-27']])
    store=DatasetStore(tmp_path)
    snap=store.save(frame,source='test_fixture',adjustment='raw')
    r={'created_at':'2025-06-26T18:00:00+08:00','trace':[{'id':'S1','tool':'load_history','result':{'data':snap}}]}
    data=projections(r,tmp_path)
    assert data['candles'][0]['frame']['date'].tolist()==['2025-06-26']
    snap=store.save(frame,source='synthetic_fixture',adjustment='raw',synthetic=True)
    r['trace'][0]['result']['data']=snap
    assert not projections(r,tmp_path)['candles']


def test_public_example_pdf_from_navigation(tmp_path):
    example=ROOT/'examples/technology-research.json'
    if not example.exists(): return
    (tmp_path/'examples').mkdir()
    (tmp_path/'examples/technology-research.json').write_bytes(example.read_bytes())
    with patch('investment_agent.config.Settings.load',return_value=Settings(root=tmp_path)):
        app=AppTest.from_file(str(ROOT/'app.py')).run(timeout=30)
        next(b for b in app.button if b.label=='探索研究示例  ↗').click().run(timeout=30)
        assert not app.exception
        next(b for b in app.button if b.label=='生成 PDF 报告').click().run(timeout=30)
        assert not app.exception and list((tmp_path/'data/reports/exports').glob('*.pdf'))
