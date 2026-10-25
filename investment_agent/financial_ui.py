"""Statement diagnostics and a clearly labelled assumptions workbench."""
import asyncio
import json
import pandas as pd
import streamlit as st
import plotly.express as px
from .financial_model import research_financials,dcf_scenarios

def render(settings):
    st.title('财务建模')
    st.caption('公开三表 → 盈利与现金质量 → 杜邦分析 → FCFF DCF情景。计算由Python完成。')
    with st.form('financial-source'):
        a,b=st.columns(2)
        code=a.text_input('证券代码',value='002156.SZ')
        name=b.text_input('证券简称',value='通富微电')
        submit=st.form_submit_button('获取财报并分析',type='primary')
    if submit:
        with st.spinner('读取公开三表并检索官方披露…'):
            try: st.session_state.financial_analysis=asyncio.run(research_financials(settings,code,name))
            except Exception as exc: st.error(str(exc) if hasattr(exc,'code') else '财报读取失败：'+type(exc).__name__)
    data=st.session_state.get('financial_analysis')
    if not data:
        st.info('先获取一家公司的财报。仅数据分析不会调用语言模型。'); return
    st.subheader(data['name']+' · '+data['symbol'])
    st.caption('数据取得日 '+data['as_of']+'。'+data['coverage_note'])
    rows=data['analysis']['rows']
    if not rows:
        st.warning('本轮未解析出结构化三表；请查看来源状态。不能据此判定公司未披露财报。')
    tabs=st.tabs(['三表与质量','估值情景','来源与缺口'])
    with tabs[0]:
        annual=[r for r in rows if r['period'].endswith('-12-31')]
        interim=[r for r in rows if not r['period'].endswith('-12-31')]
        period_type=st.radio('报告口径',['年度','中期'],horizontal=True)
        selection=annual if period_type=='年度' else interim
        st.caption('中期为累计口径，不年化；同比仅比较上年同期。')
        table=[]
        def money(v,key):
            return v[key]/1e8 if v.get(key) is not None else None
        for row in selection:
            v=row['values']
            table.append({'报告期':row['period'],'营收(亿元)':money(v,'revenue'),
                '归母净利(亿元)':money(v,'profit_parent'),
                '经营现金流(亿元)':money(v,'operating_cashflow'),
                '现金资本开支(亿元)':money(v,'capex_cash'),
                '资产负债率':row['debt_to_assets'],'合并利润现金比':row['cash_conversion']})
        if table: st.dataframe(pd.DataFrame(table),hide_index=True,use_container_width=True)
        if selection:
            r=selection[0]
            if r['dupont_roe'] is not None:
                st.markdown(f"**杜邦分析 · {r['period']}**：净利率 {r['net_margin']:.2%} × 资产周转 {r['asset_turnover']:.2f} × 权益乘数 {r['equity_multiplier']:.2f} = ROE **{r['dupont_roe']:.2%}**")
                st.caption('合并净利润 / 期初期末平均合并权益，不等于公告归母加权ROE。')
            else: st.info('杜邦分析待补字段：'+'、'.join(r['missing_for_dupont']))
            if r['cash_after_capex'] is not None:
                st.write(f"经营现金流减现金资本开支：{r['cash_after_capex']/1e8:.2f}亿元")
                st.caption('这是现金余额指标，未直接称为FCFF或FCFE。')
    with tabs[1]:
        annual=next((r for r in rows if r['period'].endswith('-12-31') and r['values'].get('revenue')),None)
        if not annual:
            st.info('DCF需要可核验的完整年度营收。当前数据不足，先补取年报，不用中报乘二代替。')
        else:
            st.caption(f"基期：{annual['period']}，营收 {annual['values']['revenue']/1e8:.2f}亿元。以下默认百分比仅演示操作，不代表公司指引或合理估值假设。")
            with st.form('financial-dcf'):
                defaults={'revenue_growth':('未来年度营收增速',10.),'operating_margin':('正常化EBIT利润率',15.),
                    'tax_rate':('现金税率',25.),'depreciation_ratio':('折旧摊销/营收',5.),'capex_ratio':('资本开支/营收',8.),
                    'working_capital_ratio':('非现金经营营运资本/营收',15.),'wacc':('WACC',10.),'terminal_growth':('永续增长率',2.)}
                cols=st.columns(2); a={}
                for i,(key,(label,default)) in enumerate(defaults.items()):
                    a[key]=cols[i%2].number_input(label+' (%)',value=default,step=.5,key='dcf-'+key)/100
                a['basis']=st.text_area('假设依据与出处',placeholder='逐项注明公开数据、公司指引或你的情景判断；投资假设不等于事实。')
                confirmed=st.checkbox('我已核对正常化盈利与投入假设，了解这些不是自动预测')
                model=st.form_submit_button('计算三种情景与敏感性')
            if model:
                if not confirmed: st.warning('请先核对并确认假设。')
                else:
                    try:
                        result=dcf_scenarios(annual['values']['revenue'],annual['period'],a)
                        st.caption(result['scenario_note'])
                        for label,r in result['scenarios'].items():
                            if 'error' in r: st.warning(label+'：'+r['message']); continue
                            st.write(f"{label}情景 · 经营企业价值 {r['enterprise_value']/1e8:.2f}亿元 · 终值占比 {r['terminal_pv_share']:.1%}")
                        values=pd.DataFrame(result['sensitivity'])
                        values['企业价值(亿元)']=values.enterprise_value/1e8
                        st.plotly_chart(px.density_heatmap(values,x='terminal_growth',y='wacc',z='企业价值(亿元)',histfunc='avg',text_auto='.1f',labels={'terminal_growth':'永续增长率','wacc':'WACC'}),width='stretch')
                        base=result['scenarios']['基准']
                        if 'forecasts' in base:
                            with st.expander('逐年现金流与计算依据'): st.dataframe(pd.DataFrame(base['forecasts']),hide_index=True)
                        st.warning('经营企业价值不是股权市值或每股目标价。净债务、少数股东权益、非经营资产和稀释股数尚需另行核验。')
                        st.download_button('下载模型与假设',json.dumps(result,ensure_ascii=False,indent=2),file_name='financial-model.json')
                    except Exception as exc: st.error(str(exc) if hasattr(exc,'code') else '模型输入无效。')
    with tabs[2]:
        for doc in data['official_disclosures'].get('accepted',[])[:12]:
            st.markdown(f"[{doc['title']}]({doc['url']}) · {doc.get('published_at','时间未确认')}")
        with st.expander('接口与解析状态'): st.json({'requests':data['requests'],'fallbacks':data['fallbacks'],'official_status':data['official_disclosures'].get('error')})
        st.markdown('[FCFF方法：NYU Damodaran](https://pages.stern.nyu.edu/~adamodar/New_Home_Page/littlebook/cashflows.htm) · [杜邦方法：CFI](https://corporatefinanceinstitute.com/resources/accounting/dupont-analysis/)')
