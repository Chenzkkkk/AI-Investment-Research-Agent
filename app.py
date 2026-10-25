"""Local-only Chinese UI; all research executes independently of Codex."""
import asyncio
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import streamlit as st
from investment_agent.agent import ResearchAgent
from investment_agent.common import DataError
from investment_agent.config import Settings, DEEPSEEK_MODEL_LABEL

st.set_page_config(page_title="知衡 · 投资研究", page_icon="◈", layout="wide")
from investment_agent.visual_theme import apply_theme,hero,icon
apply_theme()

settings = Settings.load()
from investment_agent.report_ui import render_source_route, render_research
from investment_agent.report_presentation import load_packet, linked_text, chat_links
if render_source_route(settings.root):
    st.stop()
if st.session_state.pop('intel_go_chat', False):
    st.session_state['workspace'] = '研究对话'
    st.session_state['advanced_workspace'] = '不打开'
def clear_advanced():
    st.session_state['advanced_workspace'] = '不打开'
def return_chat():
    st.session_state['advanced_workspace']='不打开'
    st.session_state['workspace']='研究对话'
with st.sidebar:
    st.markdown('<div class="zh-brand">'+icon('radar')+'知衡研究台</div>',unsafe_allow_html=True)
    st.caption("AI · 机器人 · 前沿科技")
    page = st.radio("工作区", ["研究对话", "研究看板", "前沿情报"],key='workspace',on_change=clear_advanced,
        format_func=lambda x:{'研究看板':'发现','研究对话':'研究','前沿情报':'情报'}[x])
    with st.expander('更多工具'):
        extra=st.selectbox('专业工作区',['不打开','财务建模','研究记录','A股联合研究','官方账户','数据与技能','因子与回测','模拟组合','日报与复盘'],key='advanced_workspace')
        if extra!='不打开': page=extra
    if extra!='不打开':
        st.button('返回研究对话',on_click=return_chat,use_container_width=True)
    st.divider()
    with st.expander('研究设置'):
        settings.enable_miaoxiang = st.toggle("妙想 Skills 与 MCP", value=False)
        st.caption('默认使用公开数据；妙想可作为增强来源。')
        settings.enable_public = st.toggle("公开数据补充", value=settings.enable_public)
        settings.enable_choice = st.toggle("Choice（需已开通 API 权限）", value=settings.enable_choice)
        if not settings.enable_choice:
            st.caption("Choice 当前关闭，不触发登录。")
        settings.max_calls = st.slider("每次研究最多数据请求", 12, 300, 180,step=6)
        st.caption("缓存命中不计入新请求；原文读取和模型调用单独记录。")
        settings.reasoning_effort = st.selectbox("推理强度", ["low", "high", "max"],
            index=["low", "high", "max"].index(settings.reasoning_effort),
            format_func=lambda value:{"low":"低 · low", "high":"高 · high（默认）", "max":"最高 · max"}[value],
            key="deepseek_reasoning_effort",
            help="作用于接下来发起的研究。默认high，max通常需要更长时间。")
    with st.expander('运行状态'):
        st.write("DeepSeek：" + ("凭证已配置" if settings.deepseek_key else "待配置"))
        st.write("模型：" + (DEEPSEEK_MODEL_LABEL if settings.model == "deepseek-flash" else settings.model))
        st.caption("API模型："+settings.model+" · 思考已启用 · 推理强度："+settings.reasoning_effort)
        st.write("妙想：" + ("凭证已配置" if settings.mcp_key and settings.skills_key else "待配置"))
        st.caption("凭证存在不代表服务额度或权限可用。")
    with st.expander('来源恢复'):
        st.caption('妙想额度或权限恢复后可清除熔断；不会购买服务。')
        if st.button('重新允许妙想请求'):
            from investment_agent.research_runtime import ResearchStore
            with ResearchStore(settings.root).connect() as db:
                db.execute("DELETE FROM circuits")
            st.success('已清除暂停状态，下一次研究将按来源开关使用。')
    st.caption('本地工作区 · 数据按来源日期展示')

if page == "前沿情报":
    from investment_agent.intelligence.ui import render as render_intelligence
    render_intelligence(settings)
    st.stop()

if page == "研究看板":
    from investment_agent.dashboard_ui import render_home
    render_home(settings)
    st.stop()

if page == "研究记录":
    from investment_agent.dashboard_ui import render_history
    render_history(settings)
    st.stop()

if page == "财务建模":
    from investment_agent.financial_ui import render
    render(settings)
    st.stop()

if page == "A股联合研究":
    from investment_agent.ashare_ui import render as render_ashare
    render_ashare(settings)
    st.stop()

if page == "官方账户":
    from investment_agent.accounts.ui import render as render_accounts
    render_accounts(settings)
    st.stop()

if page != "研究对话":
    from investment_agent.quant_ui import render
    render(page,settings)
    st.stop()

from investment_agent.chat_ui import render as render_chat
render_chat(settings)
