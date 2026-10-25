"""Conversation-first workspace. Rendering never calls an LLM."""
import json
import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from .research_runtime import ResearchStore
from .research_jobs import ResearchJobs
from .sector_screen import PROFILES


@st.cache_resource
def jobs(root): return ResearchJobs(root)


STATUS={'checked':'已核查','conflict':'存在矛盾','missing':'缺少数据','not_applicable':'不适用','not_run':'尚未执行'}


def result_cards(result,settings):
    if result.get('review_status')=='model_evidence_review_completed':
        st.caption('已进行模型证据审阅；这不是审计或自动事实认证，关键判断仍可展开原文核查。')
    st.markdown(result.get('report','尚未生成正文。'))
    for item in result.get('trace',[]):
        if item['tool'] not in ('financial_model','execute_professional_skill'): continue
        data=item['result'].get('data',{})
        if data.get('skill')=='technical-analysis':
            snapshot=data['data'].get('snapshot',{}).get('data',{})
            if snapshot.get('id'):
                with st.expander('K线与量价 · 来源快照'):
                    from .quant_data import DatasetStore
                    frame,meta=DatasetStore(settings.root).load(snapshot['id'])
                    frame=frame.tail(180)
                    fig=go.Figure(go.Candlestick(x=frame['date'],open=frame['open'],high=frame['high'],low=frame['low'],close=frame['close']))
                    fig.update_layout(template='plotly_dark',height=350,xaxis_rangeslider_visible=False,margin=dict(l=5,r=5,t=15,b=15))
                    st.plotly_chart(fig,width='stretch',key='kline-'+result.get('task_id',result['created_at'])+'-'+item['id'])
                    st.caption(str(meta.get('source'))+' · '+str(meta.get('adjustment'))+' · 截止 '+meta['end'])
        if data.get('skill')=='financial-quality': data=data['data']
        rows=data.get('analysis',{}).get('rows',[])
        if rows:
            with st.expander('财务趋势 · 已披露数据'):
                frame=pd.DataFrame([{'报告期':r['period'],'营业收入':r['values'].get('revenue'),
                    '经营现金流':r['values'].get('operating_cashflow'),'归母净利润':r['values'].get('profit_parent')} for r in rows]).sort_values('报告期')
                # Annual and cumulative interim periods must never share a misleading trend line.
                annual=frame[frame['报告期'].str.endswith('12-31')]
                if len(annual)>1:
                    fig=go.Figure()
                    for key in ('营业收入','经营现金流','归母净利润'):
                        fig.add_trace(go.Bar(x=annual['报告期'],y=annual[key]/1e8,name=key))
                    fig.update_layout(template='plotly_dark',height=320,yaxis_title='亿元 · 完整年度',margin=dict(l=10,r=10,t=15,b=20),legend=dict(orientation='h'))
                    st.plotly_chart(fig,width='stretch',key='finance-chart-'+result.get('task_id',result['created_at'])+'-'+item['id'])
                st.dataframe(frame,width='stretch',hide_index=True)
        model=data.get('data',{}) if data.get('skill') else data
        if isinstance(model,dict) and model.get('model')=='linked-three-statement':
            with st.expander('三表预测 · 显式假设情景'):
                st.dataframe(pd.DataFrame(model['rows']).drop(columns=['assumptions'],errors='ignore'),hide_index=True,width='stretch')
    coverage=result.get('research_coverage',{})
    if coverage.get('items'):
        with st.expander('研究覆盖与重要未知'):
            for item in coverage['items']:
                st.write(f"**{item['label']}** · {STATUS.get(item['status'],item['status'])}")
                if item.get('claim'): st.caption(item['claim'])
                if item.get('watch_next'): st.caption('继续观察：'+item['watch_next'])
            st.caption(coverage.get('note',''))
    with st.expander('计算、来源与调用记录'):
        for entry in result.get('trace',[]):
            st.write(entry['tool']); st.json(entry['result'],expanded=False)
    st.caption(f"{result.get('created_at','')} · {result.get('model','')} · 推理 {result.get('reasoning_effort','high')} · 数据请求 {result.get('data_calls',0)}")
    st.download_button('下载研究报告',result.get('report',''),file_name='研究报告.md',key='download-'+result.get('task_id',result.get('report_file',result['created_at'])))


def render(settings):
    runner=jobs(str(settings.root)); store=runner.store
    conversations=store.conversations()
    with st.sidebar:
        if st.button('＋ 新研究',width='stretch'):
            st.session_state.research_conversation=store.create_conversation(); st.rerun()
        if not conversations:
            st.session_state.research_conversation=store.create_conversation(); conversations=store.conversations()
        ids=[x['id'] for x in conversations]
        if st.session_state.get('research_conversation') not in ids: st.session_state.research_conversation=ids[0]
        cid=st.selectbox('研究会话',ids,format_func=lambda x:next(c['title'] for c in conversations if c['id']==x),key='research_conversation')
    profile=store.setting('investment_profile',{'style':'value','constraints':{}})
    st.markdown('## 从一个问题，开始研究')
    st.caption('前沿消息 · 公司财务 · 估值与风险。研究过程和证据会自动保存在本机。')
    controls=st.columns([3,2])
    with controls[0]:
        settings.investment_style=st.selectbox('本轮投资偏好',list(PROFILES),index=list(PROFILES).index(profile.get('style','value')),
            format_func=lambda k:PROFILES[k]['label']+' · '+PROFILES[k]['horizon'],key='chat_style')
    with controls[1]:
        show_evidence=st.toggle('显示研究侧栏',value=True)
    with st.expander('我的投资约束（可选）'):
        constraints=profile.get('constraints',{})
        horizon=st.text_input('投资期限',value=constraints.get('horizon',''))
        risk=st.selectbox('风险承受', ['未提供','较低','中等','较高'],index=['未提供','较低','中等','较高'].index(constraints.get('risk','未提供')))
        liquidity=st.text_input('流动性需要与已有持仓',value=constraints.get('liquidity',''),help='仅保存在本机；发起研究时作为用户约束传给已配置模型。')
        constraints={'horizon':horizon,'risk':risk,'liquidity':liquidity}
    profile={'style':settings.investment_style,'constraints':constraints,'reasoning_effort':settings.reasoning_effort}
    if store.setting('investment_profile')!=profile: store.set_setting('investment_profile',profile)
    task=store.latest(cid); active=task and task['status'] in ('queued','running')
    with st.container(key='research-columns'):
        main,aside=st.columns([7,3]) if show_evidence else (st.container(),None)
    messages=store.messages(cid)
    question=None
    with main:
        question=st.chat_input('问一个行业、一家公司，或一个关于未来的判断…',disabled=bool(active))
        if not messages:
            st.markdown('### 你想了解什么？')
            st.caption('可以从模糊想法开始，Agent 会确定范围并检查容易遗漏的因素。')
            prompts=['机器人产业哪些公司真正实现了商业化？比较现金流、估值与风险。','分析工业富联的财务质量，并告诉我最需要进一步核实的因素。','最近有哪些可能改变产业竞争格局的新产品？']
            for i,p in enumerate(prompts):
                if st.button(p,key='prompt-'+str(i),width='stretch'):
                    tid=store.create_task(cid,p,profile); runner.start(tid,settings); st.rerun()
        for m in messages:
            with st.chat_message(m['role']):
                if m['role']=='user': st.markdown(m['content'])
                else: result_cards(m['result'],settings)
        if task:
            @st.fragment(run_every='2s' if active else None)
            def task_status():
                current=store.task(task['id'])
                if active and current['status'] not in ('queued','running'): st.rerun()
                if current['status'] in ('queued','running'):
                    st.info('研究中 · '+current['progress'])
                    st.caption('可以关闭浏览器；本地网页服务运行时会继续。服务重启后可从证据继续。')
                    if st.button('停止后续研究',key='cancel-'+task['id']): store.cancel(task['id']); st.rerun()
                elif current['status'] in ('failed','interrupted','cancelled'):
                    st.warning(current['progress'])
                    if st.button('从已保存进度继续',key='resume-'+task['id']):
                        store.resume(task['id']); runner.start(task['id'],settings); st.rerun()
            task_status()
    if aside is not None:
        with aside:
            with st.container(key='research-evidence'):
                st.markdown('#### 研究依据')
                st.caption('专业方法固定计算，Agent 根据问题选择调查路径。')
                for title,detail in [('前沿情报','政策、技术、产品与商业化'),('公司研究','商业模式、三表与治理'),('估值与风险','情景、反证与失效条件')]:
                    with st.container(border=True):
                        st.write('**'+title+'**'); st.caption(detail)
                completed=[m['result'] for m in messages if m['role']=='assistant']
                if completed:
                    unknown=completed[-1].get('research_coverage',{}).get('important_unknowns',[])
                    if unknown:
                        st.markdown('#### 仍需关注')
                        for item in unknown: st.caption('• '+item['label']+'：'+STATUS.get(item['status'],''))
                with st.expander('专业能力目录'):
                    from .professional_skills import catalog
                    for spec in catalog(): st.caption(spec['title']+' · '+('证据调查' if spec['kind']=='workflow' else '计算/数据'))
    if st.session_state.get('intel_transfer'):
        st.info('来自前沿情报：'+st.session_state.intel_transfer)
        if st.button('研究这条消息',disabled=bool(active)): question=st.session_state.pop('intel_transfer')
    if question:
        tid=store.create_task(cid,question,profile); runner.start(tid,settings); st.rerun()
