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
    with st.expander('导入官方财报 PDF 补充证据'):
        uploaded=st.file_uploader('官方财报（只在本机保存）',type=['pdf'])
        issuer=st.text_input('PDF发行人名称')
        period=st.text_input('报告期（YYYY-MM-DD）',value='2025-12-31')
        if st.button('解析本地财报',disabled=uploaded is None or not issuer):
            try:
                from .financial_documents import extract_pdf
                import hashlib
                content=uploaded.getvalue()
                if len(content)>40_000_000: raise ValueError('PDF超过40MB限制')
                path=settings.root/'data/financial-model/imports'/(hashlib.sha256(content).hexdigest()+'.pdf')
                path.parent.mkdir(parents=True,exist_ok=True); path.write_bytes(content)
                result=extract_pdf(path,period,issuer)
                path.with_suffix('.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
                st.json(result)
                st.caption('用户导入资料尚未核对官方发布日；不自动作为历史时点已核验数据。')
            except Exception as exc: st.error('解析未完成：'+str(exc))
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
    tabs=st.tabs(['三表与质量','估值情景','来源与缺口','三表预测'])
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
    with tabs[3]:
        linked_workbench(rows)


def linked_workbench(rows):
    from .professional_models import three_statement,equity_bridge
    st.caption('非金融企业年度情景。金额全部为元，比率为小数。空值必须补齐；基期不平衡会阻止计算。编辑假设后重新计算不调用语言模型。')
    annual=next((r for r in rows if r['period'].endswith('12-31')),None)
    if not annual: st.info('需要完整年度基期。'); return
    signature=json.dumps(annual['values'],sort_keys=True,ensure_ascii=False)
    if st.session_state.get('linked_company')!=signature:
        st.session_state.pop('linked_result',None)
        st.session_state['linked_company']=signature
    mapping={'revenue':'revenue','cash':'cash','receivables':'receivables','inventory':'inventory',
             'payables':'payables','ppe':'ppe','other_assets':None,'debt':None,'other_liabilities':None,'equity':'equity_total'}
    with st.form('linked-forecast'):
        st.write('**基期资产负债与收入 · '+annual['period']+'**')
        base_frame=pd.DataFrame([{'字段':k,'金额（元）':annual['values'].get(v) if v else None} for k,v in mapping.items()])
        base_edit=st.data_editor(base_frame,disabled=['字段'],hide_index=True,width='stretch',key='linked-base')
        st.caption('其他资产/负债与有息债务需按附注分类；现金不能混入受限资金。不能为配平随意填写。')
        st.write('**逐年假设 · 默认数值仅演示，须修改并说明依据**')
        demo=dict(growth=.10,gross_margin=.25,opex_ratio=.10,tax_rate=.25,dso=60.,dio=60.,dpo=45.,
                  capex_ratio=.08,depreciation_ratio=.10,interest_rate=.05,payout=.20,
                  new_debt=0.,debt_repayment=0.,equity_issue=0.,basis='')
        edited=st.data_editor(pd.DataFrame([demo.copy() for _ in range(3)]),hide_index=True,num_rows='dynamic',width='stretch',key='linked-drivers')
        st.caption('growth=增长；gross_margin=毛利率；opex_ratio=不含折旧的费用率；dso/dio/dpo=应收/存货/应付天数；basis=每年假设依据。')
        confirmed=st.checkbox('我已核对基期、单位与每年假设；这些是研究情景')
        calculate=st.form_submit_button('重算三表与融资缺口')
    if calculate:
        if not confirmed: st.warning('请先核对输入。')
        else:
            try:
                base={r['字段']:r['金额（元）'] for _,r in base_edit.iterrows()}
                st.session_state.linked_result=three_statement(base,edited.to_dict('records'))
            except Exception as exc:
                st.session_state.pop('linked_result',None); st.error(str(exc) if hasattr(exc,'code') else '请检查数字和假设格式。')
    if st.session_state.get('linked_result'):
        result=st.session_state.linked_result
        st.dataframe(pd.DataFrame(result['rows']).drop(columns=['assumptions']),hide_index=True,width='stretch')
        st.caption('balance_residual为平衡差额；funding_gap为未安排融资的缺口。'+' '.join(result['limits']))
        st.download_button('下载三表情景与假设',json.dumps(result,ensure_ascii=False,indent=2),file_name='linked-statements.json')
    with st.expander('企业价值 → 股权价值与每股价值'):
        st.caption('各项均需自行核验，金额为元、股数为股；不把合并账面现金直接认定为可分配现金。')
        with st.form('equity-bridge'):
            entries={k:st.number_input(label,value=None,key='bridge-'+k) for k,label in {
                'enterprise_value':'经营企业价值','debt':'同口径有息债务（含适用租赁负债）','cash':'可分配非经营现金',
                'minority_interest':'少数股东权益价值','nonoperating_assets':'其他非经营资产价值','shares':'估值日稀释股数'}.items()}
            basis=st.text_input('桥接口径与来源依据')
            convert=st.form_submit_button('计算股权桥接')
        if convert:
            if not basis.strip(): st.warning('请填写来源和口径依据。')
            else:
                try:
                    result=equity_bridge(entries); result['basis']=basis
                    st.metric('情景每股价值（元）',f"{result['value_per_share']:.2f}"); st.json(result,expanded=False)
                except Exception as exc: st.error(str(exc) if hasattr(exc,'code') else '桥接输入无效。')
