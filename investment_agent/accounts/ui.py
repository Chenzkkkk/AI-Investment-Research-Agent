"""Read-only official account workspace in the existing Streamlit app."""
from dataclasses import asdict, replace
import json
from pathlib import Path
import uuid
import streamlit as st
from .models import Source, Status, Target, masked
from .sync_service import AccountSyncService
from .budget import BudgetPolicy
from .manual_import import blank_template, read_manual
from ..common import DataError, symbol


def amount(value):
    return "unavailable" if value is None else f"{value:,.2f}"


def private_rows(rows):
    if rows is None:
        st.info("unavailable：来源尚未提供这项数据，不能认定为空仓或无委托。")
        return
    if not rows:
        st.caption("来源明确返回空列表。")
        return
    redacted = [{k:(masked(str(v)) if k in {"account_id","order_id","execution_id"} else v) for k,v in row.items()} for row in rows]
    st.dataframe(redacted, hide_index=True, use_container_width=True)


def render(settings):
    service = AccountSyncService(settings.root)
    st.title("官方账户 · 只读同步")
    st.caption("在官方界面由本人交易；这里仅接收账户数据与生成研究建议。与「本地研究账本」分别存储。")
    st.info("账户来源分别核验。只有官方授权且已验证的连接才可读取账户；当前页面不会自动登录或交易。")
    targets = service.targets()
    key = st.selectbox("账户连接", [t.key for t in targets], format_func=lambda k:next(t.name+" · "+t.source for t in targets if t.key==k))
    target = service.target(key)
    token = f"account-startup-{key}"
    # At startup read only through a previously configured live provider. Otherwise only read local connection state.
    if token not in st.session_state:
        st.session_state[token] = True
        if service.ready(key):
            service.sync(key)
    if st.button("同步账户", type="primary"):
        service.sync(key)
    view = service.view(key)
    snapshot = service.snapshot(key)
    cols = st.columns(3)
    cols[0].metric("账户状态", view["status"])
    cols[1].metric("官方账户ID（脱敏）", view["account_id_masked"])
    cols[2].metric("最近成功采集", view["last_success_at"] or "unavailable")
    st.write("账户名称：", view["account_name"] or "unavailable")
    st.caption(f"来源 {view['source']} · 审批 {view['approval']} · 连接 {view['connection_status']} · 通道 {view['transport'] or '未配置'}")
    st.caption("来源数据时间："+(view["source_data_at"] or "unavailable")+"；接收时间与官方数据更新时间分开记录。")
    if view["status"] != Status.CONNECTED_VERIFIED.value:
        st.warning(view["message"]+" 当前不能报告同账户同步验收通过。")
    if snapshot:
        for warning in snapshot.warnings:
            st.caption(warning)

    board, strategy, identity, imports, connection = st.tabs(["组合看板","策略本金与风险","身份与数据核验","手工数据导入","连接配置"])
    with board:
        cash = snapshot.cash if snapshot else {}
        cols = st.columns(4)
        for col,(label,field) in zip(cols,[("总资产","total_assets"),("可用资金","available"),("冻结资金","frozen"),("证券市值","market_value")]):
            col.metric(label,amount(cash.get(field)))
        st.caption("浮动盈亏："+amount(cash.get("floating_pnl"))+"；组合收益："+amount(cash.get("return_pct"))+"%（缺少来源收益时不以100万元反推）")
        st.subheader("当前持仓")
        private_rows(snapshot.positions if snapshot else None)
        st.subheader("委托")
        private_rows(snapshot.orders if snapshot else None)
        st.subheader("执行回报与成交")
        st.caption("GM exec_type=15才是成交，19是撤单拒绝；未知类型不计作成交。日内查询不等于完整历史。")
        private_rows(snapshot.executions if snapshot else None)
        st.caption(f"本地已去重的执行回报数：{view['known_execution_reports']}；没有唯一回报ID的数据不会凑数。")
    with strategy:
        policy = service.policy(key)
        symbols = sorted({r.get("symbol") for r in (snapshot.positions or [])+(snapshot.orders or []) if r.get("symbol")}) if snapshot else []
        with st.form("account-policy-"+key):
            capital = st.number_input("策略本金预算（元）",min_value=1.0,max_value=1e10,value=float(policy.capital),step=1000.0)
            max_position = st.slider("策略最大仓位",0.0,1.0,float(policy.max_position),0.05)
            single = st.slider("单只股票资金上限占策略本金",0.0,1.0,float(policy.single_stock),0.05)
            reserve = st.slider("策略现金保留比例",0.0,1.0,float(policy.cash_reserve),0.05)
            attribution = st.multiselect("明确计入本策略的官方持仓/委托（整只标的）",symbols,default=[s for s in policy.strategy_symbols if s in symbols])
            if st.form_submit_button("保存策略配置"):
                service.save_policy(key,BudgetPolicy(capital,max_position,single,reserve,attribution))
                st.success("已保存本地预算；没有修改官方账户资产。")
        risk = service.analyze(key)
        cols = st.columns(3)
        for col,(label,field) in zip(cols,[("策略本金","strategy_capital"),("策略已占用","strategy_used"),("策略剩余额度","strategy_remaining")]):
            col.metric(label,amount(risk.get(field)))
        st.caption("官方可用资金："+amount(risk["official_available"])+"；单一持仓占账户资产最高比例："+amount(risk.get("max_single_account_weight")))
        st.write(risk["reason"])
        with st.form("account-sizing-"+key):
            code = st.text_input("研究股票代码",value="600519",max_chars=12)
            price = st.number_input("本人填写的研究参考价（元/股，非成交价）",min_value=0.01,value=100.0,step=0.01)
            if st.form_submit_button("计算研究仓位上限"):
                try:
                    analysis = service.analyze(key,code=symbol(code),price=price)
                    st.json(analysis)
                except DataError as exc:
                    st.error(str(exc))
        st.caption("账户未核验、数据过期、归属不明或字段不足时，数量保持unavailable。此处没有交易按钮。股票行情与基本面可在「研究对话」查询。")
    with identity:
        st.write("本人在官方账户界面核对唯一ID、总资产、可用资金、证券市值及全部持仓。仅名称一致不通过。")
        st.caption("提供含时区的官方查看时间，与采集时间相差不超过120秒。持仓核对含symbol、quantity、sellable、cost_price、market_value。")
        with st.form("account-review-"+key):
            evidence = st.text_area("官方界面核对记录 JSON（无Token、密码或Cookie）",height=180,
                placeholder='{"source":"EASTMONEY_WEB_SIM","account_id":"官方唯一ID","observed_at":"含时区时间","cash":{},"positions":[]}')
            if st.form_submit_button("保存本次核对结果"):
                try:
                    st.json(service.verify(key,json.loads(evidence)))
                except (DataError,ValueError,TypeError):
                    st.error("核对记录无效或不匹配。请提供官方唯一ID和完整的本次数据核对项。")
        st.caption("手工导入即使核对一致，仍标MANUAL_IMPORT_ONLY；只有已配置的官方只读通道、当前快照和逐项核验同时通过，才标CONNECTED_VERIFIED。")
    with imports:
        st.warning("这只是本人提供官方导出/可见页面数据的导入入口，不代表已实现自动网页同步。没有宣称官网具备此JSON格式的导出功能。")
        st.download_button("下载空白规范模板",json.dumps(blank_template(target.source),ensure_ascii=False,indent=2),
                           file_name="account_snapshot_blank.json",mime="application/json")
        uploaded = st.file_uploader("导入本人整理的账户快照JSON",type=["json"])
        if uploaded is not None and st.button("接收手工快照"):
            try:
                service.ingest(key,read_manual(uploaded.getvalue()))
                st.success("已接收手工数据；不是官方自动连接。")
                st.rerun()
            except DataError as exc:
                st.error(str(exc))
        st.caption("null表示unavailable；[]仅在官方明确显示无持仓/无委托/无成交时填写。不要上传安装包、登录资料或整页HTML。")
    with connection:
        st.caption("以下只保存本机连接配置，不在任何平台申请、创建或更换官方账户。")
        with st.form("account-connection-"+key):
            account_id = st.text_input("本人从官方页面核对的唯一账户ID（不自动填充）",value=target.account_id or "",type="password")
            approval = st.selectbox("本账户审批状态（本人记录）",["UNKNOWN","APPROVED","PENDING_APPROVAL"],index=["UNKNOWN","APPROVED","PENDING_APPROVAL"].index(target.approval))
            interval = st.number_input("只读同步间隔（秒，最低60秒）",min_value=60,max_value=86400,value=target.interval_seconds)
            stale = st.number_input("快照过期阈值（秒）",min_value=60,max_value=86400,value=target.stale_seconds)
            auto = st.checkbox("启用页面打开期间的定时只读同步",value=target.auto_sync,disabled=not service.ready(key))
            if st.form_submit_button("保存连接配置"):
                try:
                    service.save_target(replace(target,account_id=account_id.strip() or None,approval=approval,
                                                interval_seconds=int(interval),stale_seconds=int(stale),auto_sync=auto))
                    st.success("配置已保存；不会自动开通API权限。")
                except DataError as exc:
                    st.error(str(exc))
        if not service.ready(key):
            st.caption("当前无可运行的官方只读通道，定时同步保持关闭，不反复尝试Choice登录。")
        with st.expander("登记另一个已经存在的官方账户连接（不创建官方账户）"):
            with st.form("account-new-target"):
                name = st.text_input("已有账户名称")
                source = st.selectbox("已确认的官方产品",[Source.EASTMONEY_WEB_SIM.value,Source.DONGCAI_GM_SIM.value,Source.CHOICE_PORTFOLIO.value])
                new_id = st.text_input("该产品官方唯一账户ID",type="password")
                if st.form_submit_button("保存独立本地连接"):
                    if not name.strip() or not new_id.strip():
                        st.error("需先从官方确认产品、名称和唯一账户ID。")
                    else:
                        service.save_target(Target(uuid.uuid4().hex,source,name.strip(),new_id.strip()))
                        st.success("已登记独立来源；未关联或迁移原组合。")
    current = service.target(key)
    if service.ready(key):
        @st.fragment(run_every=2)
        def poll():
            # Poll local state only. Official API cadence is enforced in its SDK worker.
            state = service.view(key)
            st.caption("账户读取状态："+state["status"]+" · "+(state["last_success_at"] or "unavailable"))
            if state.get("last_received_at") != view.get("last_received_at"):
                st.rerun(scope="app")
        poll()
