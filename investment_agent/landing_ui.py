"""A product introduction, deliberately independent of private research history."""
import base64
from pathlib import Path
import streamlit as st
from .visual_theme import icon


def navigate(page):
    st.session_state['workspace']=page
    st.session_state['advanced_workspace']='不打开'


@st.cache_data
def hero_image():
    path=Path(__file__).parent/'assets/hero.png'
    return base64.b64encode(path.read_bytes()).decode() if path.is_file() else ''


def render(settings):
    art=hero_image()
    st.markdown(f'''<section class="zh-launch" style="background-image:linear-gradient(90deg,rgba(5,13,23,.95),rgba(5,13,23,.72) 37%,rgba(5,13,23,.05) 75%),url(data:image/png;base64,{art})">
<div class="zh-launch-copy"><div class="zh-eyebrow">ZHIHENG &nbsp; / &nbsp; INTELLIGENCE INTO INSIGHT</div>
<h1>知衡<br><span>投资研究</span></h1>
<p>面向各行业的投资研究。<br>整合情报、财报与市场数据。</p>
<div class="zh-launch-tag">对话研究 · 数据分析 · 证据报告</div></div>
<div class="zh-launch-foot">从研究问题到完整分析报告</div></section>''',unsafe_allow_html=True)
    a,b,c=st.columns([1.15,1.15,1])
    with a: st.button('开始与 Agent 研究  →',type='primary',width='stretch',on_click=navigate,args=('研究对话',))
    with b: st.button('探索研究示例  ↗',width='stretch',on_click=navigate,args=('研究看板',))
    with c: st.button('打开我的报告库',width='stretch',on_click=navigate,args=('报告库',))
    st.markdown('<div class="zh-product-heading"><span>一套完整的研究工作流</span><small>DISCOVER · RESEARCH · REVIEW</small></div>',unsafe_allow_html=True)
    cards=[('radar','01 / DISCOVER','前沿情报','追踪政策、产业、技术、产品与商业化。区分原始发布、人物观点和后续进展。'),
        ('layers','02 / RESEARCH','公司与行业分析','分析公司财报、现金流与估值情景，结合市场行情和反面证据，检查研究遗漏。'),
        ('chart','03 / REVIEW','研究报告与复盘','结论、财务图表、K 线与来源汇成完整报告。保留历史快照，支持 PDF 导出与后续复盘。')]
    st.markdown('<div class="zh-product-grid">'+''.join(f'<article class="zh-product-card">{icon(i)}<small>{num}</small><h3>{title}</h3><p>{body}</p></article>' for i,num,title,body in cards)+'</div>',unsafe_allow_html=True)
    with st.container(border=True):
        left,right=st.columns([3,1])
        with left:
            st.markdown('### 报告内容')
            st.caption('行情与股票对比 · 三张报表与财务质量 · 估值情景 · 支持与反面证据 · 判断失效条件')
        with right: st.button('浏览前沿情报 →',width='stretch',on_click=navigate,args=('前沿情报',))
    st.caption('知衡 · 本地投资研究工作台。展示材料注明日期与来源；研究判断不代表收益承诺。主视觉为 AI 生成的概念图。')
