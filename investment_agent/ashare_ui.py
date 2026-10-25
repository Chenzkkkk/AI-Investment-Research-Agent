"""Unified research workspace; no trade/account actions."""
import asyncio
from dataclasses import replace
from datetime import date, timedelta
import json

import pandas as pd
import streamlit as st

from .ashare_research import TECH_SAMPLE, run_research
from .ashare_sources import SOURCE_DIRECTORY
from .common import now


def render(settings):
    st.title('A股联合研究')
    st.caption('妙想数据与本地量化计算 → 基本面 / 技术 / 公告 → 乐观与审慎情景 → 风险复核')
    st.write('平时直接在「研究对话」说出问题即可。本页用于指定样本、查看详细证据和历史报告。')
    with st.expander('研究范围与数据标准'):
        st.write('基本面：三表、扣非、现金流、ROE、杠杆、应收/存货/合同负债、研发与估值。'
                 '技术面：MA20/60、Wilder RSI14、MACD、ATR、布林带、成交量与流动性。'
                 '消息面：公告、政策和新闻分层，显示原文抓取状态、发布日期与未知项。')
        st.dataframe(pd.DataFrame(SOURCE_DIRECTORY), hide_index=True, use_container_width=True)
        st.caption('这是来源目录，不代表每个站点已接通。实际可用性以每次研究证据为准。')
    default_codes = ','.join(x['code'][:6] for x in TECH_SAMPLE)
    default_names = ','.join(x['name'] for x in TECH_SAMPLE)
    with st.form('ashare_research_form'):
        from .sector_screen import PROFILES
        mode=st.radio('研究方式',['行业筛选后深入研究','指定股票研究'])
        style=st.selectbox('投资偏好',list(PROFILES),index=list(PROFILES).index('value'),format_func=lambda k:PROFILES[k]['label']+' · '+PROFILES[k]['horizon'])
        topic = st.text_input('研究主题',value='AI、机器人与前沿科技')
        codes = st.text_input('指定证券代码（仅指定股票模式使用，最多5只）',value=default_codes)
        names = st.text_input('证券简称（与代码一一对应）',value=default_names)
        today = date.fromisoformat(now()[:10])
        cutoff = st.date_input('研究截止日',value=today,max_value=today)
        budget = st.slider('本次数据请求上限',12,120,60,step=6)
        st.caption('一次研究有6个分析阶段，引用不合格时每阶段至多修正1次；另读取公开披露索引和至多8份原文，会使用现有API额度。'
                   '报告保存在本机，可查看数据证据；缺数据会保留缺口，不补造。')
        submitted = st.form_submit_button('运行联合研究',type='primary')
    if submitted:
        try:
            code_list = [x.strip() for x in codes.replace('，',',').split(',') if x.strip()]
            name_list = [x.strip() for x in names.replace('，',',').split(',') if x.strip()]
            if len(code_list)!=len(name_list): raise ValueError('代码与简称数量必须一致。')
            universe = [{'code':c,'name':n} for c,n in zip(code_list,name_list)]
            if mode=='行业筛选后深入研究': universe=[]
            with st.status('准备联合研究',expanded=True) as status:
                result = asyncio.run(run_research(replace(settings,max_calls=budget,investment_style=style),universe,cutoff.isoformat(),
                    progress=lambda text:status.write(text),topic=topic))
                status.update(label='研究完成' if result['status']=='completed' else '部分完成，请查看失败原因',
                              state='complete' if result['status']=='completed' else 'error')
                st.session_state.ashare_result = result
        except Exception as exc:
            st.error(str(exc) if isinstance(exc,ValueError) or hasattr(exc,'code') else '研究未完成：'+type(exc).__name__)
    root = settings.root/'data/ashare/runs'
    saved = sorted(root.glob('*/result.json'),key=lambda p:p.stat().st_mtime,reverse=True) if root.exists() else []
    if saved:
        chosen = st.selectbox('查看已保存研究',[p.parent.name for p in saved],key='ashare_saved_run')
        if st.button('打开这份报告'):
            st.session_state.ashare_result = json.loads((root/chosen/'result.json').read_text(encoding='utf-8'))
    result = st.session_state.get('ashare_result')
    if result:
        st.caption(f"{result['created_at']} · {result['model']} · 推理{result['reasoning_effort']} · {result['status']}")
        if result['status']!='completed': st.warning('部分阶段未完成，不能视为完整研究。')
        from .report_presentation import load_packet, normalize_markdown
        from .report_ui import render_research
        packet=load_packet(settings.root,result['run_id'])
        report=render_research(result,packet,settings.root) if packet else normalize_markdown(result['report'])
        if not packet: st.markdown(report)
        st.download_button('下载联合研究报告',report,file_name='A股联合研究.md',mime='text/markdown')
        with st.expander('来源状态、指标和引用检查'):
            st.json({k:result.get(k) for k in ('coverage','missing_dimensions','quality_status','model_calls')})
            packet_path = root/result['run_id']/'packet.json'
            if packet_path.is_file():
                packet = json.loads(packet_path.read_text(encoding='utf-8'))
                st.json(packet['evidence'])
                st.download_button('下载证据清单',packet_path.read_bytes(),file_name='evidence-packet.json')
