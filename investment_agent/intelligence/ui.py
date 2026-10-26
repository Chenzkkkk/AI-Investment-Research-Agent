"""Interactive intelligence workspace. Reading never triggers collection or model calls."""
import asyncio
from datetime import datetime,timedelta
from html import escape
import json
from pathlib import Path
import subprocess
import sys

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from .store import Store,DOMAINS,TZ,now,dumps
from .service import IntelligenceService
from ..visual_theme import hero,kpis,section
from ..dashboard_charts import style


def start_collection(root,no_model=False):
    command=[sys.executable,'-m','investment_agent.intelligence.worker','--force']
    if no_model: command.append('--no-model')
    # Worker owns the OS lock. No shells or user-controlled command fragments.
    subprocess.Popen(command,cwd=str(root),stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                     creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))


def title(event): return (event.get('analysis') or {}).get('title_zh') or event['title']


def source_links(event):
    for a in event['articles']:
        st.link_button(a['title'][:110],a['url'],use_container_width=True)
        st.caption(f"{a['tier']} · {a['coverage']} · {a['published'] or '发布时间待核实'}")


def card(event,store,key,reason=''):
    a=event.get('analysis') or {}; marks=event.get('marks',{})
    with st.container(border=True):
        st.caption(f"{event['domain']}  /  {event['region']}  /  {(event['published'] or '时间待核实')[:16]}")
        st.markdown('**'+escape(title(event))+'**')
        summary=a.get('summary_zh') or '等待中文整理；可打开详情阅读来源标题与原始材料。'
        st.write(summary[:180]+'…' if len(summary)>180 else summary)
        if reason: st.caption('入选理由：'+reason)
        if a.get('unknowns'): st.caption('待确认：'+a['unknowns'][0])
        cols=st.columns([2,1,1])
        if cols[0].button('展开事件 →',key=key+'open',use_container_width=True):
            st.session_state.intel_event=event['id']; st.rerun()
        if cols[1].button('★ 已收藏' if marks.get('star') else '☆ 收藏',key=key+'star',use_container_width=True):
            store.mark(event['id'],'star',not marks.get('star')); st.rerun()
        if cols[2].button('已读' if marks.get('read_at') else '标记已读',key=key+'read',use_container_width=True):
            store.mark(event['id'],'read_at'); st.rerun()


def refs(ref_ids,event):
    by_id={a['id']:a for a in event['articles']}
    for ref in dict.fromkeys(ref_ids):
        a=by_id.get(ref)
        if a: st.link_button('来源 · '+a['title'][:65],a['url'])


def detail(settings,store,eid):
    event=store.event(eid)
    if not event:
        st.warning('找不到该事件'); return
    if event['analysis_status'].startswith('已合并:'):
        event=store.event(event['analysis_status'].split(':',1)[1])
    if st.button('← 返回情报工作台'):
        st.session_state.pop('intel_event',None); st.rerun()
    hero(title(event),'原始资料、事件进展与可能影响分别展示。','EVENT / EVIDENCE')
    a=event.get('analysis') or {}
    st.caption(f"发布时间 {event['published'] or '待核实'} · 首次收录 {event['first_seen']} · 更新 {event['updated']} · {event['analysis_status']}")
    st.caption('事件发生时间：'+((event.get('analysis') or {}).get('occurred_at') or '未单独核实；不可用发布时间替代'))
    section('事实与出处',symbol='check')
    st.write(a.get('summary_zh') or '中文摘要尚未完成，下方是已获取的原始资料。')
    for fact in a.get('facts',[]):
        st.write('• '+fact['text']); refs(fact['refs'],event)
    for s in a.get('statements',[]):
        with st.container(border=True):
            st.markdown('**原文引述 · '+escape(s['speaker'])+'**')
            st.caption(s['identity']+' · '+s['context'])
            st.text(s['quote']); st.write('中文释义：'+s['interpretation_zh']); refs([s['ref']],event)
    with st.expander('所有原文与读取状态',expanded=not bool(a)):
        source_links(event)
        for art in event['articles']:
            with st.expander(art['title'][:90]): st.text((art['body'] or art['excerpt'])[:12000])
    section('事件演进',symbol='pulse')
    for revision in event['revisions']:
        art=revision['article']
        st.caption(revision['recorded']+' · 本库记录版本（不是事件发生时间）')
        st.write(art['title']+' · '+art['coverage'])
    for conflict in a.get('conflicts',[]):
        st.warning(conflict['text']); refs(conflict['refs'],event)
    section('可能影响',symbol='layers')
    impacts=a.get('impacts',[])
    if not impacts: st.info('尚无足够材料形成影响分析。')
    for index,impact in enumerate(impacts):
        with st.container(border=True):
            st.caption('模型推断 · '+impact['horizon'])
            st.markdown(f"**变化**：{escape(impact['change'])}\n\n**传导机制**：{escape(impact['mechanism'])}\n\n**涉及对象**：{escape(impact['target'])}")
            st.write('成立条件：'+impact['condition']); refs(impact['refs'],event)
    if impacts:
        fig=go.Figure()
        for i,impact in enumerate(impacts):
            labels=[impact['change'],impact['mechanism'],impact['target']]
            fig.add_trace(go.Scatter(x=[0,1,2],y=[-i]*3,mode='lines+markers',text=labels,
                hovertemplate='%{text}<extra></extra>',marker=dict(size=18,color=['#59d6d0','#72a7ff','#e8b96b']),line=dict(color='#36516a'),showlegend=False))
        style(fig,220); fig.update_xaxes(tickvals=[0,1,2],ticktext=['变化','机制','涉及对象']);fig.update_yaxes(visible=False)
        st.plotly_chart(fig,use_container_width=True,key='intel-impact')
        st.caption('关系图表达上述假设链条；节点可悬停查看，不代表已证实的因果关系。')
    if a.get('watch_next'):
        st.markdown('**接下来观察**')
        for text in a['watch_next']: st.write('• '+text)
    with st.expander('对照已有股票 K 线（手动选择关联，不推断涨跌原因）'):
        from ..dashboard_ui import saved_reports
        from ..report_presentation import load_packet
        from ..dashboard_charts import load_bars,candle_figure
        reports=saved_reports(settings.root)
        if not reports: st.caption('暂无已有股票研究快照。')
        else:
            # saved_reports returns summaries; use the run directory identity directly.
            runs=sorted((settings.root/'data/ashare/runs').glob('*/packet.json'),reverse=True)[:20]
            selected=st.selectbox('已保存的研究快照',['不展示']+[p.parent.name for p in runs],key='intel-kline-run')
            if selected!='不展示':
                packet=load_packet(settings.root,selected)
                if packet:
                    frames,metadata,errors=load_bars(settings.root,packet)
                    if frames:
                        code=st.selectbox('对照股票',list(frames),key='intel-kline-code')
                        fig=candle_figure(frames[code],90)
                        pub=(event['published'] or '')[:10]
                        dates=frames[code].date.astype(str).tolist()
                        if pub in dates:
                            fig.add_shape(type='line',x0=pub,x1=pub,y0=0,y1=1,xref='x',yref='paper',line=dict(color='#e8b96b',dash='dot'))
                        st.plotly_chart(fig,use_container_width=True,key='intel-kline')
                        st.caption('事件公开日期：'+(pub or '未知')+'；'+('已标注对应交易日。' if pub in dates else '不在该快照交易日中，未移动到其他日期。'))
                        st.caption('行情截止 '+str(packet['cutoff'])+'；时间相邻不等于因果关系。')
                    else: st.info('所选研究无可核验行情快照。')
    section('继续研究',symbol='focus')
    buttons=st.columns(3)
    if buttons[0].button('稍后阅读',use_container_width=True): store.mark(event['id'],'later'); st.success('已保存')
    if buttons[1].button('交给研究 Agent',use_container_width=True):
        st.session_state.intel_chat_seed=f"请先调用 get_intelligence_event 读取事件 {event['id']}，研究：{title(event)}。区分事实、观点和推断，并说明下一步需要补充的证据。"
        st.session_state.pop('intel_event',None);st.rerun()
    if buttons[2].button('进入股票研究',use_container_width=True):
        st.session_state.intel_transfer=f"基于情报事件 {event['id']}（{title(event)}），先读取事件证据，再判断有无可验证的A股关联。若有关联，补查真实财务和行情进行研究；不确定时说明缺口，不强行推荐股票。"
        st.session_state.intel_go_chat=True; st.rerun()
    st.download_button('导出事件与来源 JSON',dumps(event),'event-'+event['id']+'.json','application/json')


def run_panel(settings,store):
    cfg=store.settings(); rows=store.sources(); usage=store.usage()
    kpis([('今日分析',usage['events'],f"日上限 {cfg['daily_events']}",'layers'),
          ('输入 token',usage['input'],f"预算 {cfg['daily_input_tokens']}",'chart'),
          ('输出 token',usage['output'],f"预算 {cfg['daily_output_tokens']}",'pulse'),
          ('来源',sum(r['status'].startswith('已接通') for r in rows),f"候选与失败均单列 / 共 {len(rows)}",'radar')])
    with st.form('intel-settings'):
        enabled=st.toggle('启用本机后台采集',cfg['enabled'])
        model=st.toggle('自动整理新事件（使用现有 DeepSeek）',cfg['model_enabled'])
        effort=st.selectbox('后台推理强度',['low','high','max'],index=['low','high','max'].index(cfg['reasoning_effort']))
        cols=st.columns(3)
        count=cols[0].number_input('每日最多分析事件',0,200,cfg['daily_events'])
        inp=cols[1].number_input('每日输入 token 上限',0,2000000,cfg['daily_input_tokens'],step=10000)
        out=cols[2].number_input('每日输出 token 上限',0,400000,cfg['daily_output_tokens'],step=1000)
        cols=st.columns(3)
        feed=cols[0].number_input('订阅间隔 / 分钟',15,1440,cfg['feed_minutes'])
        page=cols[1].number_input('机构页面间隔 / 分钟',30,1440,cfg['page_minutes'])
        research=cols[2].number_input('科研订阅间隔 / 分钟',720,10080,cfg['research_minutes'])
        if st.form_submit_button('保存后台设置'):
            store.configure(dict(enabled=enabled,model_enabled=model,reasoning_effort=effort,daily_events=int(count),daily_input_tokens=int(inp),daily_output_tokens=int(out),
                                 feed_minutes=int(feed),page_minutes=int(page),research_minutes=int(research)))
            st.success('已保存，下次任务使用此设置。')
    st.caption('未知或失败的模型用量按预留额度保守计入；每轮最多100篇正文，后台任务互斥。')
    a,b=st.columns(2)
    if a.button('现在采集并整理',use_container_width=True): start_collection(settings.root);st.success('后台任务已请求；稍后刷新查看状态。')
    if b.button('只采集，不调用模型',use_container_width=True): start_collection(settings.root,True);st.success('只读采集任务已请求。')
    if st.button('将分析失败的事件重新排队（仍受每日预算限制）'):
        with store.db() as db:db.execute("UPDATE events SET analysis_status='待分析' WHERE analysis_status LIKE '分析失败:%'")
        st.success('已重新排队；下次后台运行处理，原始证据和旧分析仍保留。')
    st.dataframe(pd.DataFrame([dict(来源=r['config']['name'],领域=r['config']['domain'],状态=r['status'],启用=r['config']['enabled'],
        最近成功=r['success'],错误=r['error'] or '',最近条目数=r['entries']) for r in rows]),hide_index=True,use_container_width=True)
    selected=st.selectbox('管理已适配来源',[r['id'] for r in rows if r['config']['kind']!='candidate'],
                          format_func=lambda k:next(r['config']['name'] for r in rows if r['id']==k))
    cfg_source=next(r['config'] for r in rows if r['id']==selected)
    enabled_source=st.checkbox('启用该来源',cfg_source['enabled'],key='source-enabled-'+selected)
    if enabled_source!=cfg_source['enabled']: store.enable_source(selected,enabled_source);st.rerun()
    st.link_button('查看来源网站',cfg_source['url'])
    st.caption('已接通仅表示最近一次采集成功；候选源不计入实际覆盖。巨潮资讯的既有股票公告能力继续保留。')
    with st.expander('任务记录',expanded=True):
        for job in store.jobs():
            st.caption(f"{job['started']} → {job['finished'] or '运行中'} · {job['status']}")
            if job['summary']: st.json(json.loads(job['summary']),expanded=False)
    registration=store.directory/'scheduler-status.json'
    if registration.exists():
        st.caption('计划任务注册回执（注册时的状态）；实际运行以本页任务记录为准。')
        st.json(json.loads(registration.read_text(encoding='utf-8-sig')),expanded=False)
    else: st.warning('尚无计划任务注册记录；手动采集仍可使用。')


def chat(settings):
    with st.expander('向情报 Agent 追问',expanded=bool(st.session_state.get('intel_chat_seed'))):
        with st.form('intel-ask'):
            question=st.text_area('研究问题',value=st.session_state.get('intel_chat_seed',''),placeholder='最近机器人商业化有哪些实质进展？',height=90)
            submit=st.form_submit_button('基于情报库研究',type='primary')
        if submit and question.strip():
            from .research_agent import IntelligenceResearchAgent
            with st.status('正在检索情报证据…',expanded=True) as status:
                try:
                    result=asyncio.run(IntelligenceResearchAgent(settings).ask(question,progress=lambda message:status.write(message)))
                    st.session_state.intel_answer=result['report']
                    st.session_state.intel_result=result
                    status.update(label='研究报告已保存' if result['status']=='completed' else '部分完成，证据已保存',state='complete' if result['status']=='completed' else 'error')
                    st.session_state.pop('intel_chat_seed',None)
                except Exception as exc:
                    status.update(label='研究未完成',state='error'); st.error('错误类型：'+type(exc).__name__)
        st.caption('独立情报 Agent：规划 → 多源检索 → 原文核对 → 中文融合 → 反证复核。手动研究最多4次模型请求，与后台每日摘要预算分别记录。')
        if st.session_state.get('intel_answer'): st.markdown(st.session_state.intel_answer)
        if st.session_state.get('intel_result'):
            result=st.session_state.intel_result
            from pathlib import Path
            st.link_button('打开完整报告与 PDF 导出','?report='+Path(result['report_file']).stem)
            with st.expander('研究过程与用量'):
                for step in result['research_process']:st.write(step['stage']+' · '+step['status'])
                st.caption('模型调用 '+str(result['budget_usage']['model_calls'])+' 次；输入 '+str(result['budget_usage']['input_tokens'])+'，输出 '+str(result['budget_usage']['output_tokens'])+' token。')



def render(settings):
    store=Store(settings.root)
    from .materials_ui import render_library,article_detail
    if st.session_state.get('intel_article'):
        article_detail(settings,store,st.session_state.intel_article);return
    if st.session_state.get('intel_event'):
        detail(settings,store,st.session_state.intel_event);return
    hero('前沿情报','追踪真实变化，连接政策、科技、产业与新的商业可能。','GLOBAL / FRONTIER INTELLIGENCE')
    if st.button('↻ 刷新已收录数据',help='只重新读取本地情报库，不采集、不调用模型。'): st.rerun()
    page=st.radio('情报视图',['全部材料','精选简报','主题追踪','人物与机构','我的关注','来源与运行'],horizontal=True,label_visibility='collapsed')
    if page=='来源与运行': run_panel(settings,store);return
    if page=='全部材料':render_library(settings,store);return
    if page=='精选简报':st.info('这是自动整理的事件精选，不代表全部信息。你可以在「全部材料」选择任何文章单独生成中文简报。')
    chat(settings)
    c=st.columns([2,1,1,1])
    query=c[0].text_input('搜索事件、机构、人物',key='intel-query')
    days=c[1].selectbox('时间范围',[1,7,30,0],format_func=lambda v:{1:'最近24小时',7:'最近7天',30:'最近30天',0:'全部（含待核实时间）'}[v],index=0 if page=='精选简报' else 1)
    domain=c[2].selectbox('领域',['全部']+DOMAINS)
    region=c[3].selectbox('来源地区',['全部','中国','美国','全球'])
    tier=st.selectbox('证据类型',['全部','原始发布','媒体报道','社区线索','项目自述（社区发现）'])
    filters={}
    if days: filters['since']=(datetime.now(TZ)-timedelta(days=days)).isoformat(timespec='seconds')
    if domain!='全部': filters['domain']=domain
    if region!='全部': filters['region']=region
    if tier!='全部': filters['tier']=tier
    rows=store.search(query,filters,limit=600)
    follows=store.follows(); sources=store.sources()
    kpis([('收录事件',len(rows),'当前筛选，最多展示600条','layers'),('中文已整理',sum(bool(r['analysis']) for r in rows),'其余仍可阅读来源','check'),
          ('已关注',len(follows),'主题 / 人物 / 机构','focus'),('接通来源',sum(r['status'].startswith('已接通') for r in sources),'详见来源与运行','radar')])
    from .research_agent import chinese
    pending=[r for r in rows if not chinese((r.get('analysis') or {}).get('summary_zh'))]
    show_pending=st.toggle('查看待整理原始线索（尚不是中文简报）',False)
    if pending:st.caption(f'{len(pending)} 条材料尚未完成中文整理；额度不足时保持待处理，不冒充已完成简报。')
    if not show_pending:rows=[r for r in rows if r not in pending]
    if page=='主题追踪':
        topic_options=sorted({t for r in rows for t in (r['analysis'] or {}).get('topics',[])})
        topic=st.selectbox('主题',['全部']+topic_options)
        if topic!='全部':
            rows=[r for r in rows if topic in (r['analysis'] or {}).get('topics',[])]
            if st.button('关注此主题'):store.follow(topic);st.success('已关注')
        if rows:
            frame=pd.DataFrame([{'日期':r['published'][:10],'领域':r['domain']} for r in rows if r['published']])
            if not frame.empty:
                counts=frame.groupby(['日期','领域']).size().reset_index(name='本库收录事件数')
                st.plotly_chart(style(px.bar(counts,x='日期',y='本库收录事件数',color='领域'),300),use_container_width=True)
                st.caption('采集覆盖变化也会影响数量；并非全网热度。下方按公开时间排列事件。')
    elif page=='人物与机构':
        entities=sorted({(e['kind'],e['name'],e['identity']) for r in rows for e in (r['analysis'] or {}).get('entities',[]) if e['kind'] in ('人物','机构','公司')})
        if entities:
            entity=st.selectbox('人物或机构（身份分开）',entities,format_func=lambda e:' · '.join(x for x in e if x))
            rows=[r for r in rows if any((e['kind'],e['name'],e['identity'])==entity for e in (r['analysis'] or {}).get('entities',[]))]
            if st.button('关注 '+entity[1]):store.follow(entity[1]);st.success('已关注；详情中仍按身份区分')
        else: st.info('当前筛选下暂无已整理的人物或机构；可先查看来源材料。')
    elif page=='我的关注':
        with st.form('intel-follow'):
            term=st.text_input('添加关注主题、人物或关键词')
            if st.form_submit_button('关注') and term.strip():store.follow(term);st.rerun()
        if follows:
            remove=st.selectbox('已关注',follows)
            if st.button('取消关注所选'):store.follow(remove,True);st.rerun()
        mode=st.radio('显示',['关注的新进展','收藏','稍后读','已读'],horizontal=True)
        if mode=='关注的新进展':
            rows=[r for r in rows if any(t.casefold() in dumps(r).casefold() for t in follows) and r['updated']>(r['marks'].get('read_at') or '')]
        else:
            key={'收藏':'star','稍后读':'later','已读':'read_at'}[mode];rows=[r for r in rows if r['marks'].get(key)]
    if not rows:
        st.info('当前范围暂无事件。可切换最近7天或全部；来源不可用与没有新消息可在「来源与运行」区分。');return
    if page=='精选简报':
        main,side=st.columns([3,1])
        ranked=sorted(rows,key=lambda r:(any(t.casefold() in dumps(r).casefold() for t in follows),bool(r['analysis']),r['published'] or ''),reverse=True)
        highlights=[]; per_domain={}
        for r in ranked:
            if per_domain.get(r['domain'],0)>=2:continue
            highlights.append(r);per_domain[r['domain']]=per_domain.get(r['domain'],0)+1
            if len(highlights)==10:break
        with main:
            section('重点简报','LAST 24H' if days==1 else 'SELECTED PERIOD','pulse')
            for r in highlights:
                followed=any(t.casefold() in dumps(r).casefold() for t in follows)
                reason=('关注主题有更新；' if followed else '')+('已有来源约束的中文整理；' if r['analysis'] else '原始材料待整理；')+'按发布时间及领域分散展示'
                card(r,store,'hi-'+r['id'],reason)
        with side:
            section('关注与覆盖',symbol='radar')
            st.write('关注：'+'、'.join(follows) if follows else '在「我的关注」中添加主题或人物。')
            latest=max((s['success'] for s in sources if s['success']),default=None)
            st.caption('最近成功采集：'+(latest or '尚无记录'))
            for s in sources:
                if s['status']=='暂时失败':st.caption(s['config']['name']+'：'+(s['error'] or '暂时失败'))
            section('仍待确认',symbol='focus')
            for r in highlights[:5]:
                unknowns=(r['analysis'] or {}).get('unknowns',[])
                if unknowns:st.write(unknowns[0])
        with st.expander('完整信息流（按公开时间）'):
            for r in rows[:60]:card(r,store,'all-'+r['id'])
    else:
        for r in rows[:60]:card(r,store,page+r['id'])
    if len(rows)>60: st.caption('信息流显示前60条；请缩小筛选或使用搜索。')
