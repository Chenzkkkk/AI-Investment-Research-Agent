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
with st.sidebar:
    st.markdown('<div class="zh-brand">'+icon('radar')+'知衡研究台</div>',unsafe_allow_html=True)
    st.caption("AI · 机器人 · 前沿科技")
    page = st.radio("工作区", ["研究看板", "研究对话", "前沿情报"],key='workspace',on_change=clear_advanced,
        format_func=lambda x:{'研究看板':'发现','研究对话':'研究','前沿情报':'情报'}[x])
    with st.expander('更多工具'):
        extra=st.selectbox('专业工作区',['不打开','财务建模','研究记录','A股联合研究','官方账户','数据与技能','因子与回测','模拟组合','日报与复盘'],key='advanced_workspace')
        if extra!='不打开': page=extra
    st.divider()
    with st.expander('研究设置'):
        settings.enable_miaoxiang = st.toggle("妙想 Skills 与 MCP", value=False)
        st.caption('默认使用公开数据；妙想可作为增强来源。')
        settings.enable_public = st.toggle("公开数据补充", value=settings.enable_public)
        settings.enable_choice = st.toggle("Choice（需已开通 API 权限）", value=settings.enable_choice)
        if not settings.enable_choice:
            st.caption("Choice 当前关闭，不触发登录。")
        settings.max_calls = st.slider("每次研究最多数据请求", 12, 120, 60,step=6)
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
    st.caption('本地工作区 · 数据按来源日期展示')
    if st.button("清空本页对话"):
        st.session_state.messages = []
        st.rerun()

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

hero("用证据，研究每一只股票。","直接说出你的想法：行业机会、具体股票、持有顾虑。Agent 会确定范围，查证数据并形成有条件的投资建议。","DEEPSEEK / RESEARCH STUDIO")
st.caption("深入研究结合基本面、技术面、公告政策与多空复核，通常需要数分钟。「更多工具 → A股联合研究」可查看详细证据和历史报告。")
from investment_agent.sector_screen import PROFILES
settings.investment_style=st.selectbox('本轮投资偏好',list(PROFILES),index=list(PROFILES).index('value'),
    format_func=lambda k:PROFILES[k]['label']+' · '+PROFILES[k]['horizon'],key='investment_style')
st.caption('行业问题先扫描最多150只候选，批量核对财报和行情，再深入研究最多5只。缺少关键数据的股票不进入推荐候选；评分不代表收益承诺。')

with st.expander("本轮账户约束（可选）"):
    include_account = st.checkbox("允许本轮DeepSeek研究使用脱敏账户摘要与策略预算",value=False)
    if include_account:
        from investment_agent.accounts.sync_service import AccountSyncService
        account_targets = AccountSyncService(settings.root).targets()
        settings.account_context_key = st.selectbox("本轮使用的账户",[t.key for t in account_targets],
            format_func=lambda k:next(t.name+" · "+t.source for t in account_targets if t.key==k))
        st.caption("仅发送当前选择的快照摘要，不发送Token、账户ID或委托ID；未连接/未核验状态会随摘要提供。")

examples = ["AI产业链有哪些值得长期研究的公司？请先广泛筛选，再比较现金流和估值", "机器人从技术演示到商业化，哪些公司值得关注？请核验真实收入和竞争优势"]
columns = st.columns(2)
selected = None
for column, text in zip(columns, examples):
    if column.button(text, use_container_width=True):
        selected = text
if "messages" not in st.session_state:
    st.session_state.messages = []


def show_result(result: dict) -> None:
    report = None
    for entry in result.get('trace',[]):
        if entry['tool']=='joint_stock_research' and entry['result'].get('data',{}).get('status')=='partial':
            st.warning('联合研究部分阶段未完成；下方保留已完成内容及具体失败原因。')
        if entry['tool']=='joint_stock_research' and entry['result'].get('data',{}).get('run_id'):
            joint=entry['result']['data']
            packet=load_packet(settings.root,joint['run_id'])
            if packet:
                if result.get('account_constraints'):
                    st.info('本轮使用了授权账户约束；详细额度见下方数据证据。')
                report=render_research(joint,packet,settings.root)
    if report is None:
        report=linked_text(result['report'],chat_links(result,settings.root))
        st.markdown(report)
    effort_label = " · 推理 " + result["reasoning_effort"] if result.get("reasoning_effort") else ""
    st.caption(f"{result['created_at']} · {result['model']}{effort_label} · 新数据请求 {result['data_calls']} 次")
    st.download_button("下载研究报告", report, file_name="投资研究报告.md", mime="text/markdown",
                       key="report-" + result.get("report_file", result["created_at"]))
    with st.expander("查看本轮数据证据与调用状态"):
        for entry in result["trace"]:
            st.markdown(f"**[{entry['id']}] {entry['tool']}**")
            st.json(entry["result"])
            for artifact in entry["result"].get("metadata", {}).get("artifacts", []):
                path = Path(artifact).resolve()
                if path.is_relative_to((ROOT / "data").resolve()) and path.is_file():
                    st.download_button("下载原始来源报告", path.read_bytes(), file_name=path.name,
                                       key="artifact-" + result.get("report_file", result["created_at"]) + str(path))


for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        if message["role"] == "user":
            st.markdown(message["content"])
        else:
            show_result(message["result"])

question = st.chat_input("研究一个行业、一家公司，或一个关于未来的判断…")
question = selected or question
if st.session_state.get('intel_transfer'):
    st.info('来自前沿情报的研究问题：'+st.session_state.intel_transfer)
    if st.button('开始研究此事件与股票的关联',type='primary'):
        question=st.session_state.pop('intel_transfer')
if question:
    history = [{"role": m["role"], "content": m.get("content", m.get("result", {}).get("report", ""))}
               for m in st.session_state.messages[-6:]]
    st.session_state.messages.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)
    with st.chat_message("assistant"):
        status = st.status("正在启动本轮研究…", expanded=False)
        try:
            agent = ResearchAgent(settings)
            result = asyncio.run(agent.ask(question, progress=lambda label: status.update(label=label), history=history))
            status.update(label="本轮研究已结束", state="complete")
            show_result(result)
            st.session_state.messages.append({"role": "assistant", "result": result})
        except Exception as exc:
            status.update(label="本轮研究未完成", state="error")
            st.error(str(exc) if isinstance(exc, DataError) else "运行失败：" + type(exc).__name__)
            st.caption("没有生成虚构数据；请检查来源状态或稍后重试。")
