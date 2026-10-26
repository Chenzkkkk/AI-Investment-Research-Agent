"""Shared report reader for chat, history and exact evidence deep links."""
import json
from pathlib import Path
import re

import pandas as pd
import streamlit as st

from .common import safe
from .report_presentation import (STAGE_LABELS, STATUS_LABELS, RUN_PATTERN, load_packet,
    evidence_links, linked_text, review_text, coverage_rows, source_markdown, report_markdown,
    public_url, markdown_link)


def render_research(result,packet,root=None):
    from .config import ROOT
    from .dashboard_ui import render_visuals
    from .visual_theme import hero
    root=Path(root or ROOT)
    links=evidence_links(packet)
    hero(packet['topic']+'研究报告','价格、财务与多空观点，一份可追溯的研究。',packet.get('data_mode','A-SHARE RESEARCH'))
    st.caption(f"研究截止 {packet['cutoff']} · {STATUS_LABELS.get(result['status'],result['status'])}")
    if packet.get('data_mode'): st.caption('数据方案：'+packet['data_mode'])
    missing=[r['stock']+'的'+('结构化财务数据' if r['dimension']=='fundamental' else '技术数据')
             for r in packet.get('statuses',[]) if r['status']=='unavailable']
    if missing: st.warning('本次未取得：'+'、'.join(missing)+'。请结合这些缺口阅读建议。')
    source_errors=[r.get('error',{}) for r in packet.get('source_errors',[]) if r.get('error')]
    if source_errors:
        st.warning('来源限制：'+'；'.join(dict.fromkeys(e.get('message',e.get('code','查询失败')) for e in source_errors)))
    render_visuals(packet,root,key='report-'+packet['run_id'])
    if packet.get('screening'):
        from .report_presentation import screening_rows
        audit=packet['screening']; profile=audit['profile']
        st.markdown('### 行业筛选过程')
        st.write(f"{profile['label']} · {profile['horizon']} ｜覆盖 {audit['candidate_count']} 只 → 取得财报 {audit['financial_coverage']} 只 → 通过门槛 {audit['eligible_count']} 只 → 深入研究 {len(audit['selected_codes'])} 只")
        frame=pd.DataFrame(screening_rows(audit))
        st.dataframe(frame.head(10),hide_index=True,use_container_width=True)
        with st.expander('全部候选、排除原因与评分规则'):
            st.dataframe(frame,hide_index=True,use_container_width=True)
            st.write(audit['formula']); st.write('风格权重：',profile['weights'])
            for limitation in audit['limitations']: st.caption(limitation)
            st.download_button('下载全部筛选记录',frame.to_csv(index=False).encode('utf-8-sig'),file_name='行业筛选记录.csv',key='screen-'+packet['run_id'])
        failures=[r for r in audit.get('source_attempts',[]) if not r.get('ok')]
        if failures:
            messages=list(dict.fromkeys((r.get('error') or {}).get('message','部分查询未成功') for r in failures))
            st.warning('数据源提示：'+'；'.join(messages)+'。下方仅使用实际取得的数据。')
    st.markdown('### 结论与建议')
    st.markdown(review_text(result.get('reports',{}).get('review','最终建议阶段尚未完成；下方保留已完成的分析。'),packet))
    st.markdown('### 数据覆盖')
    rows=coverage_rows(packet)
    if rows: st.table(pd.DataFrame(rows))
    st.caption(packet.get('sample_note',''))
    st.markdown('### 分析依据')
    for key,label in STAGE_LABELS:
        if key in result.get('reports',{}):
            with st.expander(label):
                content=linked_text(result['reports'][key],links)
                # Stage-local headings must not dwarf the report's own hierarchy.
                content=re.sub(r'(?m)^#{1,3}\s+', '#### ',content)
                st.markdown(content)
    with st.expander('来源与数据限制'):
        st.markdown(source_markdown(packet))
        st.caption('原文链接打开发布页面；本地指标链接打开该次计算证据。链接可追溯不代表全部模型观点已通过事实核验。')
    if result.get('error'): st.warning(result['error'].get('message','部分研究尚未完成。'))
    return report_markdown(packet,result)


def evidence_detail(item,packet):
    st.markdown('## '+item.get('subject','数据来源'))
    st.caption(STATUS_LABELS.get(item.get('status'),item.get('status',''))+' · 研究截止 '+packet['cutoff'])
    data=item.get('data',{})
    url=public_url(data.get('url'))
    if url: st.markdown(markdown_link('打开对应原文',url))
    if item.get('kind')=='technical':
        labels={'as_of':'行情日期','symbol':'证券代码','close':'收盘价（元）','ma20':'20日均线','ma60':'60日均线',
            'rsi14_wilder':'RSI（14日）','macd_dif':'MACD DIF','macd_dea':'MACD DEA','atr14_wilder':'ATR（14日）',
            'return20_pct':'20日价格收益（%）','return60_pct':'60日价格收益（%）','peTTM':'市盈率（TTM）','pbMRQ':'市净率（MRQ）',
            'adjustment':'复权方式','dataset_id':'历史数据快照'}
        rows=[{'指标':label,'取值':str(round(data[key],4) if isinstance(data.get(key),float) else data.get(key,'未取得'))} for key,label in labels.items()]
        st.table(pd.DataFrame(rows))
    elif item.get('kind')=='fundamental':
        from .ashare_metrics import FIELDS
        for period in data.get('periods',[])[:4]:
            with st.expander(period['period']+' 财务数据',expanded=period==data['periods'][0]):
                st.table(pd.DataFrame([{'指标':FIELDS.get(key,[key])[0],'来源值':str(value)} for key,value in period.get('raw',{}).items()]))
                disclosure=public_url(period.get('disclosure_index',{}).get('url'))
                if disclosure: st.markdown(markdown_link('该报告期官方披露',disclosure))
                for source in period.get('source_tables',[]):
                    url=public_url(source.get('url'))
                    if url: st.markdown(markdown_link('东方财富公开财报 · '+source['report'],url))
    for row in data.get('accepted',[]):
        url=public_url(row.get('url'))
        if url:
            st.markdown(markdown_link(row.get('title','原文'),url))
            st.caption(str(row.get('published_at','日期未提供'))+' · '+row.get('publisher',''))
            if row.get('summary'): st.write(row['summary'])
    if data.get('excerpt'): st.write(data['excerpt'])
    if data.get('limits'):
        if isinstance(data['limits'],list): st.markdown('\n'.join('- '+str(value) for value in data['limits']))
        else: st.write(data['limits'])
    with st.expander('原始证据与核验详情'):
        st.json(safe(data))


def render_source_route(root: Path) -> bool:
    params=st.query_params
    if not any(k in params for k in ('research','report','evidence','source')): return False
    if st.button('← 返回我的报告'):
        st.query_params.clear()
        st.session_state['workspace']='报告库'
        st.rerun()
    if 'research' in params:
        packet=load_packet(root,params['research'])
        if not packet:
            st.error('这份研究记录不存在或链接无效。')
            return True
        if 'evidence' in params:
            item=next((e for e in packet['evidence'] if e['id']==params['evidence']),None)
            if item: evidence_detail(item,packet)
            else: st.error('未找到对应证据。')
        else:
            path=root/'data/ashare/runs'/packet['run_id']/'result.json'
            if path.is_file():
                result=json.loads(path.read_text(encoding='utf-8'))
                report=render_research(result,packet,root)
                from .report_workspace import export_controls
                from .report_data import projections
                exported={'question':packet['topic'],'created_at':result.get('created_at',packet['cutoff']),
                    'report':report,'trace':[{'id':'S1','tool':'joint_stock_research','result':{'data':{'run_id':packet['run_id']}}}]}
                export_controls(exported,root,key='joint-export',visual_data=projections(exported,root))
            else: st.info('该研究尚未生成报告。')
        return True
    report_id=params.get('report','')
    source=params.get('source','')
    base=(root/'data/reports').resolve()
    path=(base/(report_id+'.json')).resolve()
    if not re.fullmatch(r'[a-f0-9]{32}',report_id) or (source and not re.fullmatch(r'S\d+',source)) or not path.is_relative_to(base) or not path.is_file():
        st.error('数据来源链接无效。')
        return True
    saved=json.loads(path.read_text(encoding='utf-8'))
    if not source:
        from .report_workspace import render_report
        render_report(saved,root)
        return True
    item=next((r for r in saved.get('trace',[]) if r['id']==source),None)
    if item:
        from .reader_evidence import render_evidence
        st.write(saved.get('question',''))
        st.caption('生成于 '+saved.get('created_at',''))
        render_evidence(item)
    else: st.error('未找到对应数据来源。')
    return True
