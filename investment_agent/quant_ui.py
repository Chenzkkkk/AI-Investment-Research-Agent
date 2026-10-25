"""Chinese phase-two workbench. Mutations require a deliberate local button."""
import asyncio
from datetime import date, timedelta
import json
import uuid

import pandas as pd
import streamlit as st

from .backtest import BacktestConfig
from .common import DataError
from .quant import QuantLab
from .quant_data import collect_history
from .service import DataService


def choose_dataset(lab):
    items = lab.store.list()
    if not items:
        st.info("请先到“数据与技能”获取历史日线或导入CSV。")
        return None
    chosen = st.selectbox("历史数据快照", range(len(items)), format_func=lambda i:
        f"{'合成示例 · ' if items[i]['synthetic'] else ''}{','.join(items[i]['symbols'])} · {items[i]['start']}—{items[i]['end']} · {items[i]['id'][:8]}")
    meta = items[chosen]
    st.caption(f"来源 {meta['source']} · {meta['rows']}行 · {meta['adjustment']} · 数据日期 {meta['end']}")
    if meta["synthetic"]: st.warning("当前是合成数据，仅供功能演示，不能当作市场结论。")
    return meta


def display_backtest(report):
    m = report["data"]["metrics"]
    a,b,c,d = st.columns(4)
    a.metric("区间模拟收益",f"{m['total_return_pct']:.2f}%")
    b.metric("最大回撤",f"{m['max_drawdown_pct']:.2f}%")
    c.metric("模拟成交笔数",m["trades"])
    d.metric("未成交",m["rejected_orders"])
    st.line_chart(pd.DataFrame(report["data"]["curve"]).set_index("date")[["equity","gross_reference","benchmark"]])
    st.caption("equity：含模拟成本；gross_reference：相同成交份额加回累计成本的毛价格参考（不重新投资节省费用）；benchmark：固定池等权买入持有毛价格基准。")
    st.caption(report.get("return_basis","价格收益，完整含息口径未核验"))
    if not m["trades"]: st.warning("本次没有成交。请查看未成交原因；零交易收益不能作为策略有效性证据。")
    st.write("样本外：",report["holdout"])
    st.dataframe(pd.DataFrame([{"variant":s["variant"],**s.get("metrics",{}),"unavailable":s.get("unavailable","")} for s in report["sensitivity"]]),hide_index=True)
    for label,key in [("成交明细","trades"),("未成交原因","rejected"),("信号时点","signals")]:
        with st.expander(label): st.dataframe(pd.DataFrame(report["data"][key]),hide_index=True)
    with st.expander("研究假设、费用与复现信息",expanded=True):
        st.write(report["decision"])
        for line in report["assumptions"]: st.caption(line)
        st.json({k:report[k] for k in ["config","dataset_id","dataset_sha256","engine_sha256","trials","bootstrap"]})
    st.download_button("下载完整回测结果",json.dumps(report,ensure_ascii=False,indent=2),"回测结果.json","application/json")


def render(page,settings):
    lab = QuantLab(settings.root)
    st.title({"数据与技能":"让研究有据可查","因子与回测":"检验一个可复现的策略","模拟组合":"在本地练习组合管理","日报与复盘":"记录判断，等待数据检验"}[page])
    st.caption("第二阶段 · 因子、历史模拟与研究复盘 · Choice仅在侧栏明确启用后调用")
    try:
        if page == "数据与技能":
            with st.form("history_form"):
                st.subheader("获取日线快照")
                codes = st.text_input("股票代码（逗号分隔，最多5只）","600519.SH,000858.SZ")
                start = st.date_input("开始日期",date.today()-timedelta(days=180))
                end = st.date_input("截止日期",date.today()-timedelta(days=1))
                source = st.selectbox("取数来源",["auto","public","miaoxiang","choice"],format_func=lambda x:{"auto":"自动（已启用的Choice→公开→妙想）","public":"公开历史数据","miaoxiang":"妙想MCP","choice":"Choice（需权限）"}[x])
                fetch = st.form_submit_button("获取并校验历史数据",type="primary")
            if fetch:
                with st.spinner("正在获取日线；不补造缺失数据…"):
                    result = asyncio.run(collect_history(DataService(settings),[s.strip() for s in codes.replace("，",",").split(",") if s.strip()],str(start),str(end),source))
                st.success("历史数据已保存，可在因子与回测页选择。")
                st.json(result["data"])
            with st.expander("补齐交易日历、分红检查与行业映射",expanded=True):
                target=choose_dataset(lab)
                st.caption("仅访问已启用的公开数据源，不调用Choice。原快照保留，新快照附带来源日历、分红送转事件与行业更新日期。")
                if target and st.button("补齐数据质量信息",type="primary"):
                    from .data_quality import enrich_dataset
                    with st.spinner("正在核对日历、公司行动和行业分类…"):
                        result=asyncio.run(enrich_dataset(DataService(settings),target["id"]))
                    st.success("已保存增强快照："+result["data"]["dataset"]["id"])
                    st.json(result["data"]["quality"])
                if target and st.button("用妙想补齐历史涨跌停价"):
                    from .data_quality import enrich_execution_limits
                    with st.spinner("正在逐日匹配来源涨跌停价格；不推算缺值…"):
                        result=asyncio.run(enrich_execution_limits(DataService(settings),target["id"]))
                    st.success("已保存涨跌停增强快照："+result["data"]["id"])
                    st.json(result["data"]["flags"])
            with st.expander("导入本地CSV"):
                st.caption("必需：date,symbol,open,high,low,close,volume（股）；可选amount（元）、sector、tradable、can_buy、can_sell、limit_up、limit_down。PIT财务：available_at、pe_ttm、roe_pct。")
                with st.form("import_csv"):
                    upload=st.file_uploader("UTF-8 CSV",type="csv")
                    source_name=st.text_input("原始来源与数据说明")
                    adjustment=st.selectbox("复权口径",["raw","forward","backward"])
                    synthetic=st.checkbox("这是合成数据/演示数据",value=False)
                    imported=st.form_submit_button("校验并保存CSV")
                if imported:
                    if upload is None or upload.size>10_000_000: raise DataError("CSV_SIZE","请提供10MB以内CSV。")
                    meta=lab.store.save(pd.read_csv(upload,dtype={"symbol":str,"code":str}),source=source_name,adjustment=adjustment,synthetic=synthetic,
                                        provenance={"file_name":upload.name})
                    st.success("已保存："+meta["id"])
            st.subheader("已安装的量化Skills")
            for skill in lab.catalog()["data"]["skills"]:
                with st.expander(skill["name"]):
                    path=settings.root/"research-skills"/skill["name"]/"SKILL.md"
                    st.markdown(path.read_text(encoding="utf-8-sig"))
                    st.caption("项目内固定版本，原许可证保留；Agent读取方法说明，下载脚本不自动执行。")
            st.dataframe(pd.DataFrame([{k:m[k] for k in ["id","source","start","end","rows","synthetic"]} for m in lab.store.list()]),hide_index=True)
            return

        meta=choose_dataset(lab)
        if not meta: return
        frame,_=lab.store.load(meta["id"])
        if page=="因子与回测":
            lookback=st.slider("动量/波动回看交易日",5,120,20)
            as_of=st.selectbox("观察日期",sorted(frame.date.unique(),reverse=True))
            st.subheader("多因子与量价观察")
            factors=lab.analyze(meta["id"],as_of,lookback)
            st.dataframe(pd.DataFrame(factors["data"]["ranking"]),hide_index=True)
            with st.expander("行业轮动与未纳入标的"):
                st.json({k:factors["data"][k] for k in ["sector_rotation","excluded","formula","limitations"]})
            with st.expander("交易日历、缺失日线与分红事件"):
                st.json(meta.get("provenance",{}).get("quality",{"status":"尚未补充；可在数据与技能页执行"}))
            st.subheader("历史日线模拟")
            with st.form("backtest_form"):
                cols=st.columns(3)
                cash=cols[0].number_input("初始模拟现金（元）",1000.,1e9,1e6,step=10000.)
                top=cols[1].number_input("持仓标的上限",1,5,2)
                rebalance=cols[2].number_input("每隔多少交易日调仓",1,60,5)
                max_weight=st.slider("单股目标权重上限",.05,1.,.5,.05)
                costs=st.columns(3)
                commission=costs[0].number_input("双向佣金（bp）",0.,100.,3.)
                tax=costs[1].number_input("卖出税费假设（bp）",0.,100.,5.)
                slip=costs[2].number_input("单边滑点（bp）",0.,100.,5.)
                relaxed=st.checkbox("接受缺涨跌停字段的探索性模拟（结果可能乐观）",value=False)
                price_only=st.checkbox("已知有分红送转时，仍仅研究未复权价格路径（不算含息收益）",value=False)
                st.caption("最低佣金5元/笔、固定冲击2bp、100股/手、前20日均量1%容量；以上均为研究假设。严格模式缺开盘可成交字段会拒绝成交。")
                submitted=st.form_submit_button("运行回测与样本外验证",type="primary")
            if submitted:
                with st.spinner("Python正在计算并保存完整证据…"):
                    result=lab.backtest(meta["id"],{"initial_cash":cash,"lookback":lookback,"top_n":top,"rebalance_days":rebalance,
                        "max_weight":max_weight,"commission_bps":commission,"sell_tax_bps":tax,"slippage_bps":slip,
                        "strict_limits":not relaxed,"corporate_action_policy":"price_only" if price_only else "cash_dividends"})
                st.session_state.quant_backtest=result
            if "quant_backtest" in st.session_state:
                report=st.session_state.quant_backtest
                if report["dataset_id"]==meta["id"]: display_backtest(report)

        elif page=="模拟组合":
            st.caption("账户来源：LOCAL_PAPER · 本地研究账本；不与「官方账户」自动合并，也不代表官方模拟账户余额。")
            st.caption("模拟簿仅在本机SQLite中记账，不连接证券账户。参考日线收盘价与模拟费用，不代表真实可成交价格。")
            with st.expander("新建模拟组合"):
                with st.form("create_paper"):
                    account_name=st.text_input("组合名称","我的模拟组合")
                    initial=st.number_input("初始模拟资金",1000.,1e9,1e6)
                    create=st.form_submit_button("创建本地模拟组合")
                if create:
                    lab.book.create(account_name,initial)
                    st.success("模拟组合已创建。")
            accounts=lab.book.accounts()
            if not accounts:
                st.info("先创建一个模拟组合。")
                return
            name=st.selectbox("模拟组合",[a["name"] for a in accounts])
            portfolio=lab.portfolio(name,meta["id"])
            info=portfolio["data"]
            a,b=st.columns(2)
            a.metric("模拟现金",f"{info['account']['cash']:,.2f}")
            b.metric("模拟总权益","缺估值" if info["equity"] is None else f"{info['equity']:,.2f}")
            st.dataframe(pd.DataFrame(info["positions"]),hide_index=True)
            if info["max_weight_pct"] is not None and info["max_weight_pct"]>40: st.warning("模拟单股集中度超过40%。")
            for warning in info["warnings"]: st.caption(warning)
            with st.expander("集中度、历史风险与假设冲击"):
                st.json(info.get("risk",{"note":"报价未覆盖持仓，风险值暂缺。"}))
            if st.button("生成调仓草案（不成交）"):
                st.json(lab.rebalance_plan(name,meta["id"]))
            with st.expander("逐笔模拟记账"):
                with st.form("paper_trade"):
                    code=st.selectbox("标的",meta["symbols"])
                    side=st.selectbox("方向",["buy","sell"],format_func=lambda s:"模拟买入" if s=="buy" else "模拟卖出")
                    qty=st.number_input("数量（股，100股整数倍）",100,10000000,100,step=100)
                    allow=st.checkbox("接受缺涨跌停字段的模拟记账",False)
                    confirm=st.checkbox("确认仅向本地模拟簿记录此笔成交",False)
                    submitted=st.form_submit_button("记录模拟成交")
                if submitted:
                    if not confirm: raise DataError("PAPER_CONFIRM","请勾选本地模拟记账确认。")
                    bar=frame[frame.symbol==code].iloc[-1].to_dict()
                    token=st.session_state.setdefault("paper_request_id",uuid.uuid4().hex)
                    result=lab.book.trade(name,bar,meta,side,qty,token,BacktestConfig(strict_limits=not allow))
                    st.session_state.paper_request_id=uuid.uuid4().hex
                    st.success("已记录模拟成交，刷新页可查看更新后的模拟现金/持仓。")
                    st.json(result)
            with st.expander("模拟成交记录"): st.dataframe(pd.DataFrame(info["trades"]),hide_index=True)

        elif page=="日报与复盘":
            accounts=[""]+[a["name"] for a in lab.book.accounts()]
            name=st.selectbox("日报包含模拟组合（可不选）",accounts)
            if st.button("生成快照研究日报",type="primary"):
                result=lab.daily(meta["id"],name)
                st.session_state.quant_daily=result
            if "quant_daily" in st.session_state and st.session_state.quant_daily["dataset_id"]==meta["id"]:
                report=st.session_state.quant_daily["data"]["report"]
                st.markdown(report)
                st.download_button("下载研究日报",report,"研究日报.md","text/markdown")
            with st.expander("登记未来预测"):
                with st.form("prediction"):
                    code=st.selectbox("预测标的",meta["symbols"])
                    horizon=st.number_input("登记后完整交易日周期",1,60,5)
                    prob=st.slider("上涨概率",0.,1.,.5,.05)
                    thesis=st.text_area("研究理由与失效条件")
                    submitted=st.form_submit_button("按当前时间登记预测")
                if submitted: st.json(lab.predict(meta["id"],code,horizon,prob,thesis))
            if st.button("检查成熟预测并评估"):
                st.json(lab.evaluate(meta["id"]))
            st.caption("更新日线后手动评估；未成熟保持等待，结果不覆盖。预测起点不能回填时间。")
            st.dataframe(pd.DataFrame(lab.book.predictions()),hide_index=True)
    except Exception as exc:
        st.error(str(exc) if isinstance(exc,(DataError,ValueError)) else "本地研究失败："+type(exc).__name__)
