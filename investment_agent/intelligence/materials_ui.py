"""Reader-first library: every collected article remains accessible and selectable."""
from datetime import datetime,timedelta
import hashlib
import streamlit as st
from .store import TZ
from .materials import catalog,material,brief_record,start_brief,brief_markdown
from ..visual_theme import kpis


def brief_controls(store,a,key,full=False):
    initial=brief_record(store,a['id'])
    active=initial and initial['status'] in ('queued','running')
    @st.fragment(run_every='2s' if active else None)
    def view():
        record=brief_record(store,a['id'])
        if active and record and record['status'] not in ('queued','running'):st.rerun()
        if record and record['status'] in ('queued','running'):
            st.info('正在整理你选择的材料，可切换页面；本地后台完成后自动保存。')
        elif record and record['status']=='completed' and record['fingerprint']==a['content_hash']:
            brief=record['brief']
            if full:
                st.markdown(brief_markdown(brief))
                st.caption('中文简报 · '+brief['created']+' · '+brief['coverage']+(' · 长正文已节选，非全文翻译' if brief.get('text_truncated') else ''))
                st.download_button('下载中文简报',brief_markdown(brief),file_name='中文简报-'+a['id']+'.md',key=key+'download')
            else:
                st.markdown('**'+brief['title_zh']+'**');st.write(brief['summary_zh'])
        else:
            if record and record['status']=='failed':st.warning('上次整理未完成：'+str(record['error'])+'。可手动重试。')
            if record and record['brief']:st.caption('原文已有变化，旧简报仍保留在历史版本；请重新整理。')
            label='重新生成中文简报' if record else '生成中文简报'
            if st.button(label,key=key+'translate',type='secondary'):
                start_brief(store,a['id']);st.rerun()
    view()


def article_detail(settings,store,aid):
    a=material(store,aid)
    if not a:st.error('找不到材料');return
    if st.button('← 返回全部材料'):
        for key in list(st.session_state):
            if key.startswith('materials-'):st.session_state.pop(key,None)
        st.session_state.pop('intel_article',None);st.rerun()
    st.markdown('## '+a['title'])
    st.caption(a['source_name']+' · '+a['tier']+' · '+(a['published'] or '发布时间未核实'))
    st.link_button('打开原始来源 ↗',a['url'],width='stretch')
    st.caption('单篇中文整理由已配置的 DeepSeek 完成（low），每次最多一次模型请求、16384 输出 token；独立于自动精选的每日预算，按实际模型用量计费。重复阅读不调用模型。')
    brief_controls(store,a,'detail-'+aid,True)
    if a['event_analysis']:
        with st.expander('关联事件的已有中文简报（可能汇总多篇材料）'):
            st.write(a['event_analysis'].get('title_zh',''));st.write(a['event_analysis'].get('summary_zh',''))
    with st.expander('已保存原始内容与读取范围',expanded=True):
        st.caption(a['coverage']+' · 首次收录 '+a['first_seen']+' · 更新 '+a['updated'])
        st.text(a['body'] or a['excerpt'] or '当前只有标题；原文入口始终保留。')
    if st.button('查看该事件的其他报道与时间线'):
        st.session_state.pop('intel_article',None);st.session_state.intel_event=a['event_id'];st.rerun()


def render_library(settings,store,only_briefs=False):
    st.caption('这里展示已接入来源实际收录的全部文章，不由推荐算法隐藏。范围不等于全网；仅标题、仅摘要和正文分别标记。')
    sources=store.sources();names={s['id']:s['config']['name'] for s in sources}
    cols=st.columns([2,1,1])
    query=cols[0].text_input('搜索全部材料',placeholder='原文标题、正文关键词或中文简报',key='materials-query')
    source=cols[1].selectbox('来源平台',['全部']+list(names),format_func=lambda k:names.get(k,k),key='materials-source')
    days=cols[2].selectbox('发布时间范围',[0,1,7,30],format_func=lambda k:{0:'全部历史（含未知日期）',1:'最近24小时',7:'最近7天',30:'最近30天'}[k],key='materials-time')
    from .sources import DOMAINS
    cols=st.columns(3)
    domain=cols[0].selectbox('材料领域',['全部']+DOMAINS,key='materials-domain')
    tier=cols[1].selectbox('来源类型',['全部','原始发布','媒体报道','社区线索','项目自述（社区发现）'],key='materials-tier')
    status=cols[2].selectbox('本文中文简报',['全部','尚未整理','已整理'],index=2 if only_briefs else 0,key='materials-status')
    filters={}
    for key,value in [('source',source),('domain',domain),('tier',tier)]:
        if value!='全部':filters[key]=value
    if days:filters['since']=(datetime.now(TZ)-timedelta(days=days)).isoformat(timespec='seconds')
    if status!='全部':filters['brief']='ready' if status=='已整理' else 'pending'
    signature=hashlib.sha256(repr((query,filters)).encode()).hexdigest()[:12]
    page_key='materials-page-'+signature
    result=catalog(store,query,filters,page=st.session_state.get(page_key,1))
    kpis([('资料库材料',result['total'],'已收录文章；同事件报道分别保留','layers'),
          ('当前筛选',result['count'],'全部匹配结果均可翻页查看','radar'),
          ('单篇中文简报',result['translated'],'你选择整理并保存的文章','check'),
          ('接通来源',sum(s['status'].startswith('已接通') for s in sources),'不含候选或本次失败来源','pulse')])
    st.caption('手动选择不受精选排序影响：先生成中文简报，或打开原文自行判断。自动事件简报仍在「精选简报」。')
    if not result['count']:st.info('当前筛选没有材料；可调整时间或查看「来源与运行」的接入情况。');return
    page=st.number_input('页码',min_value=1,max_value=result['pages'],value=result['page'],step=1,key=page_key)
    if page!=result['page']:result=catalog(store,query,filters,page=page)
    st.caption(f"第 {result['page']} / {result['pages']} 页 · 每页12篇 · 按发布时间排序，未知时间按首次收录排列")
    for a in result['rows']:
        with st.container(border=True):
            st.caption(a['source_name']+' · '+a['domain']+' · '+(a['published'] or '发布时间未核实')+' · '+a['coverage'])
            st.markdown('**'+a['title']+'**')
            cols=st.columns([1,1,2])
            cols[0].link_button('阅读原文 ↗',a['url'],width='stretch')
            if cols[1].button('材料详情',key='material-open-'+a['id'],width='stretch'):
                st.session_state.intel_article=a['id'];st.rerun()
            with cols[2]:brief_controls(store,a,'material-'+a['id'])
            if a['event_analysis']:
                st.caption('关联事件已有中文摘要：'+a['event_analysis'].get('summary_zh','')[:180])
            elif a['excerpt']:
                with st.expander('原始摘要（未翻译）'):st.text(a['excerpt'][:3000])
    left,right=st.columns(2)
    def go(delta):st.session_state[page_key]=max(1,min(result['pages'],page+delta))
    left.button('← 上一页',disabled=page<=1,key='materials-prev',on_click=go,args=(-1,))
    right.button('下一页 →',disabled=page>=result['pages'],key='materials-next',on_click=go,args=(1,))
