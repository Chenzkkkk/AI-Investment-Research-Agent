"""Persistent local report library and a dedicated, evidence-first reading room."""
import hashlib
import json
from pathlib import Path
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from .report_data import catalog, summary, projections, financial_frame, source_links, title
from .research_runtime import ResearchStore
from .visual_theme import hero, kpis, section


def open_report(result):
    st.session_state['open_report']=result


def export_controls(result, root, key='report', visual_data=None):
    fingerprint=hashlib.sha256(json.dumps(result,ensure_ascii=False,sort_keys=True,default=str).encode()).hexdigest()
    cache_key='pdf-'+fingerprint
    a,b=st.columns([1,1])
    with a:
        if st.button('生成 PDF 报告',key=key+'-prepare-pdf',width='stretch'):
            try:
                from .report_pdf import build_pdf
                with st.spinner('正在排版正文、图表与来源…'):
                    pdf=build_pdf(result,root,visual_data=visual_data)
                    directory=Path(root)/'data/reports/exports'; directory.mkdir(parents=True,exist_ok=True)
                    (directory/(fingerprint+'.pdf')).write_bytes(pdf)
                    st.session_state[cache_key]=pdf
            except Exception as exc:
                st.error('PDF 生成失败，已保存的报告仍可阅读。错误类型：'+type(exc).__name__)
        if cache_key in st.session_state:
            st.download_button('下载 PDF ↓',st.session_state[cache_key],file_name='知衡研究报告.pdf',mime='application/pdf',
                key=key+'-pdf',width='stretch',on_click='ignore')
    with b:
        st.download_button('下载 Markdown',result.get('report',''),file_name='知衡研究报告.md',key=key+'-md',width='stretch')
    st.caption('PDF 包含完整正文、可用的数据图表、研究覆盖和可点击的原文索引；导出不调用模型。')


def render_dashboard(data,key,root=None):
    from .dashboard_charts import candle_figure
    for packet in data.get('joint',[]):
        from .dashboard_ui import render_visuals
        render_visuals(packet,root,key=key+'-'+packet['run_id'])
        st.link_button('阅读完整联合研究与筛选依据 ↗','?research='+packet['run_id'])
    for warning in data['warnings']: st.warning(warning)
    if data['candles'] and not data.get('joint'):
        section('行情与量价','PRICE / VOLUME','pulse')
        options=list(range(len(data['candles'])))
        choice=st.selectbox('查看行情',options,format_func=lambda i:data['candles'][i]['label'],key=key+'-stock')
        item=data['candles'][choice]
        window=st.select_slider('日线观察窗口',[30,60,90,180],value=90,key=key+'-window')
        st.plotly_chart(candle_figure(item['frame'],window,True),width='stretch',key=key+'-candle',theme=None,config={'displayModeBar':False})
        st.caption(f"来源 {item['meta'].get('source')} · 复权 {item['meta'].get('adjustment')} · 截止 {item['meta'].get('end')} · 历史快照")
        with st.expander('行情数据与下载'):
            st.dataframe(item['frame'].tail(window),hide_index=True,width='stretch')
            st.download_button('下载行情 CSV',item['frame'].to_csv(index=False).encode('utf-8-sig'),file_name='行情快照.csv',key=key+'-csv')
    elif not data['candles']: st.info('本次研究没有保存可重绘的日线快照。可回到对话补充技术分析；这里不自动请求新数据。')
    if data['finances']:
        section('财务透视','STATEMENTS / CASH FLOW','layers')
        for idx,item in enumerate(data['finances']):
            frame=financial_frame(item); annual=frame[frame['报告期'].str.endswith('12-31')]
            st.markdown('#### '+item['label'])
            if len(annual)>1:
                fig=go.Figure()
                for col,color in [('营业收入','#77b6df'),('经营现金流','#54d6c3'),('归母净利润','#dfbb75')]:
                    fig.add_bar(x=annual['报告期'],y=annual[col]/1e8,name=col,marker_color=color)
                fig.update_layout(template='plotly_dark',paper_bgcolor='rgba(0,0,0,0)',plot_bgcolor='rgba(0,0,0,0)',
                    height=370,yaxis_title='亿元 · 完整年度',legend=dict(orientation='h',y=1.12),margin=dict(l=12,r=12,t=45,b=20))
                st.plotly_chart(fig,width='stretch',key=key+'-finance-'+str(idx),config={'displayModeBar':False})
            st.caption('只比较完整年度；季度累计值在明细中单独呈现，不机械年化。经营现金流与归母利润口径不同。')
            if item.get('coverage_note'): st.caption(str(item['coverage_note']))
            with st.expander('财报数据、质量诊断与估值计算'):
                st.dataframe(frame,hide_index=True,width='stretch')
                st.caption('明细金额单位：元。空白表示未取得。')
                st.download_button('下载财务 CSV',frame.to_csv(index=False).encode('utf-8-sig'),file_name='财务数据.csv',key=key+'-financial-csv-'+str(idx))
                for name,label in [('quality','质量诊断'),('dcf','估值模型'),('documents','附注核验')]:
                    if item.get(name): st.write(label); st.json(item[name],expanded=False)
        if len(data['finances'])>1:
            st.markdown('#### 公司比较 · 相同年度')
            periods=[set(financial_frame(x)['报告期']) for x in data['finances']]
            common=sorted(p for p in set.intersection(*periods) if p.endswith('12-31'))
            if common:
                period=st.selectbox('比较财报期间',common,index=len(common)-1,key=key+'-period')
                rows=[]
                for item in data['finances']:
                    row=financial_frame(item).set_index('报告期').loc[period].to_dict()
                    rows.append({'公司':item['label'],**{k:(v/1e8 if pd.notna(v) else None) for k,v in row.items()}})
                st.dataframe(pd.DataFrame(rows),width='stretch',hide_index=True); st.caption('单位：亿元；行业和合并范围仍需核验。')
            else: st.info('没有共同的完整年度，暂不并列比较。')
    else: st.info('本次研究未包含结构化财务快照。现有文字判断及原文依据保留在对应页签。')
    for idx,model in enumerate(data['models']):
        section('三表联动预测','ASSUMPTIONS / SCENARIOS','chart')
        st.dataframe(pd.DataFrame(model.get('rows',[])).drop(columns=['assumptions'],errors='ignore'),width='stretch',hide_index=True)
        with st.expander('预测假设与校验记录'): st.json(model,expanded=False)


def render_report(result,root):
    from .chat_ui import STATUS
    hero(title(result),'结论、数据与依据，在同一份研究中核对。','RESEARCH REPORT / SAVED SNAPSHOT')
    st.caption('生成于 '+str(result.get('created_at','未记录'))+' · '+str(result.get('model',''))+' · 已保存在本机')
    with st.expander('原始研究问题'): st.write(result.get('question','未记录'))
    st.markdown('**研究摘要**'); st.write(summary(result.get('report',''),350))
    st.caption('摘要为正文节选；适用条件与风险以完整报告为准。')
    coverage=result.get('research_coverage') or {}; items=coverage.get('items',[])
    sources=source_links(result); data=projections(result,root)
    kpis([('研究证据',len(result.get('trace',[])),'工具返回记录，非独立来源数','layers'),
        ('原文入口',len(sources),'去重后的可访问网址','radar'),('覆盖核查',f"{sum(x.get('status')=='checked' for x in items)} / {len(items)}",'完成程度，不代表结论可信概率','check'),
        ('可用日线',len(data['candles']),'本次保存的行情序列','pulse')])
    with st.expander('导出 PDF / Markdown'):
        export_controls(result,root,visual_data=data)
    text,charts,evidence,coverage_tab=st.tabs(['研究报告','数据看板','原文与依据','研究覆盖'])
    with text:
        if result.get('review_status')=='model_evidence_review_completed': st.caption('已完成模型证据审阅；这不是审计或自动事实认证。')
        st.markdown(result.get('report','暂无正文'))
    with charts: render_dashboard(data,'reading-room',root)
    with evidence:
        if not sources: st.info('本次没有保存可公开跳转的原文链接；下方保留工具证据。')
        from .report_presentation import markdown_link
        for item in sources: st.markdown(markdown_link(item['title'],item['url']))
        with st.expander('逐项计算与调用记录'):
            for entry in result.get('trace',[]):
                st.write(str(entry.get('id',''))+' · '+entry.get('tool',''))
                st.json(entry.get('result'),expanded=False)
    with coverage_tab:
        if not items: st.info('这份历史报告未保存结构化覆盖清单；不据此认定已完成全面核查。')
        for item in items:
            with st.container(border=True):
                st.markdown('**'+item['label']+'** · '+STATUS.get(item['status'],item['status']))
                if item.get('claim'): st.write(item['claim'])
                if item.get('watch_next'): st.caption('继续观察：'+item['watch_next'])
        st.caption(coverage.get('note',''))


def render_library(settings):
    hero('研究报告库','自动保存研究结论与证据。支持按问题检索、收藏、历史回看与报告导出。','LOCAL RESEARCH LIBRARY')
    store=ResearchStore(settings.root); favorites=set(store.setting('favorite_reports',[]))
    records=catalog(settings.root)
    a,b=st.columns([3,1])
    with a: query=st.text_input('搜索历史研究',placeholder='公司、行业，或当时提出的问题…')
    with b: only=st.toggle('只看收藏')
    selected=[r for r in records if query.casefold() in (r['title']+r['summary']).casefold() and (not only or r['kind']+':'+r['id'] in favorites)]
    st.caption(f'本机共 {len(records)} 份报告 · 当前匹配 {len(selected)} 份。自动保存不等于云备份；请保留本地 data 目录。')
    if not selected: st.info('还没有符合条件的报告。完成研究后会自动出现在这里。'); return
    page=st.number_input('页码',min_value=1,max_value=max(1,(len(selected)+11)//12),step=1)
    for r in selected[(page-1)*12:page*12]:
        ident=r['kind']+':'+r['id']
        with st.container(border=True):
            st.markdown('#### '+title({'question':r['title']})); st.caption(r['date']+' · '+('对话研究' if r['kind']=='report' else '联合研究'))
            st.write(r['summary'])
            a,b=st.columns([3,1])
            with a: st.link_button('阅读完整报告 ↗','?'+r['kind']+'='+r['id'],width='stretch')
            with b:
                if st.button('★ 已收藏' if ident in favorites else '☆ 收藏',key='favorite-'+ident,width='stretch'):
                    favorites.symmetric_difference_update({ident}); store.set_setting('favorite_reports',sorted(favorites)); st.rerun()
