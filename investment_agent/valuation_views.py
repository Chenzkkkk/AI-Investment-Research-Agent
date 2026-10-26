"""Presentation of computed valuations; never invent inputs or target prices."""
import math
import pandas as pd


def finite(x):
    return x if isinstance(x,(int,float)) and not isinstance(x,bool) and math.isfinite(x) else None


def scenario_frame(item):
    rows=[]
    for label,result in item['data'].get('scenarios',{}).items():
        a=result.get('assumptions',{})
        ev=finite(result.get('enterprise_value'))
        row={'情景':label,'经营企业价值（亿元）':ev/1e8 if ev is not None else None,
             '状态':result.get('message','已计算')}
        for key,title in [('revenue_growth','收入增速（%）'),('wacc','WACC（%）'),('terminal_growth','永续增长（%）')]:
            value=finite(a.get(key));row[title]=100*value if value is not None else None
        tv=finite(result.get('terminal_pv_share'));row['终值占比（%）']=tv*100 if tv is not None else None
        rows.append(row)
    return pd.DataFrame(rows)


def sensitivity_frame(item):
    rows=item['data'].get('sensitivity',[])
    if not rows: return pd.DataFrame()
    frame=pd.DataFrame(rows)
    if not {'wacc','terminal_growth','enterprise_value'}.issubset(frame): return pd.DataFrame()
    frame['wacc']*=100;frame['terminal_growth']*=100;frame['enterprise_value']/=1e8
    return frame.pivot_table(index='wacc',columns='terminal_growth',values='enterprise_value',dropna=False)


def valuation_markdown(item):
    """Same numeric rows and explicit assumptions for web and PDF."""
    data=item['data'];blocks=['### '+item['label']]
    if data.get('capital_bridge'):
        from .capital_bridge import bridge_markdown
        return bridge_markdown(data['capital_bridge'],data.get('equity_valuation'))
    frame=scenario_frame(item)
    if not frame.empty:
        columns=['情景','经营企业价值（亿元）','收入增速（%）','WACC（%）','永续增长（%）']
        blocks.append('| '+' | '.join(columns)+' |\n| '+' | '.join(['---']*len(columns))+' |\n'+'\n'.join(
            '| '+' | '.join((f'{row[k]:,.3f}' if isinstance(row[k],(int,float)) and pd.notna(row[k]) else '未取得' if pd.isna(row[k]) else str(row[k])) for k in columns)+' |' for row in frame.to_dict('records')))
        blocks.append('以上为经营企业价值，尚未减债、加非经营现金或调整少数股东，不能直接当成每股价格。')
        if data.get('scenario_note'): blocks.append(data['scenario_note'])
        for label,model in data['scenarios'].items():
            if model.get('error'): blocks.append(label+'：'+str(model.get('message',model['error'])));continue
            a=model.get('assumptions',{})
            blocks.append('**'+label+'假设依据**：'+str(a.get('basis','未提供，不能作为已核实估值。')))
            terms=[]
            for k,title in [('operating_margin','营业利润率'),('tax_rate','税率'),('depreciation_ratio','折旧/收入'),('capex_ratio','资本开支/收入'),('working_capital_ratio','增量营运资本/增量收入')]:
                v=finite(a.get(k))
                if v is not None: terms.append(f'{title} {v*100:.2f}%')
            blocks.append('；'.join(terms)+f"；预测 {a.get('years','未提供')} 年。")
            share=finite(model.get('terminal_pv_share'))
            if share is not None: blocks.append(f'终值占估值 {share*100:.2f}%，须核验稳态及长期投入假设。')
        sens=sensitivity_frame(item)
        if not sens.empty:
            blocks.append('**WACC / 永续增长敏感性**（经营企业价值，亿元）')
            blocks.append('| WACC / g | '+' | '.join(f'{g:g}%' for g in sens.columns)+' |\n|---|'+'---|'*len(sens.columns)+'\n'+'\n'.join(
                '| '+f'{w:g}%'+' | '+' | '.join(f'{v:,.3f}' if pd.notna(v) else '不适用' for v in row)+' |' for w,row in sens.iterrows()))
    else:
        names={'equity_value':'股权价值（单位同输入）','value_per_share':'每股价值（单位须与股数口径一致）','wacc':'WACC（小数）','cost_of_equity':'权益成本（小数）','terminal_share':'终值占比（小数）'}
        for key,label in names.items():
            value=finite(data.get(key))
            if value is not None: blocks.append(f'- {label}：{value:,.4f}')
        if data.get('valuations'):
            for metric,v in data['valuations'].items(): blocks.append(f"- {metric}：样本 {v.get('sample_size')}，中位倍数 {v.get('median')}，推导价值 {v.get('implied_value')}（{v.get('value_type')}，单位同输入）")
        inputs=item.get('assumptions',{}).get('inputs',{})
        if inputs:
            labels={'risk_free':'无风险利率','beta':'Beta','equity_risk_premium':'权益风险溢价',
                'cost_of_debt':'债务成本','tax_rate':'税率','equity_weight':'股权资本权重'}
            if inputs.get('method')=='cost_of_capital':
                lines=['| 输入假设 | 数值 |','|---|---|']
                for k,label in labels.items():
                    v=finite(inputs.get(k))
                    if v is not None: lines.append('| '+label+' | '+(f'{v:.3f}' if k=='beta' else f'{v*100:.2f}%')+' |')
                blocks.append('**显式假设与输入（计算成功不等于已核实）**\n\n'+'\n'.join(lines))
            else:
                import json
                blocks.append('**显式假设与输入（计算成功不等于已核实）**\n\n'+json.dumps(inputs,ensure_ascii=False,indent=2))
        if data.get('terminal_assumption'): blocks.append(data['terminal_assumption'])
        for limitation in data.get('limits',[]): blocks.append('- '+str(limitation))
    return '\n\n'.join(blocks)


def render_valuations(items,key):
    import streamlit as st
    import plotly.graph_objects as go
    from .visual_theme import section
    if not items:
        st.info('本次没有保存独立估值计算。财务表现与股价不是估值结论，不能据此推导目标价。')
        return
    section('估值情景与假设','VALUATION / SENSITIVITY','chart')
    for i,item in enumerate(items):
        frame=scenario_frame(item)
        if not frame.empty:
            fig=go.Figure(go.Bar(x=frame['情景'],y=frame['经营企业价值（亿元）'],marker_color=['#77b6df','#54d6c3','#dfbb75']))
            fig.update_layout(template='plotly_dark',height=300,yaxis_title='经营企业价值 / 亿元',margin=dict(l=12,r=12,t=20,b=25))
            st.plotly_chart(fig,width='stretch',key=f'{key}-valuation-{i}',config={'displayModeBar':False})
        st.markdown(valuation_markdown(item))
        with st.expander('估值计算明细 · '+item['id']):
            for label,model in item['data'].get('scenarios',{}).items():
                if model.get('forecasts'):
                    st.write(label+'逐年预测 · 金额单位：元')
                    st.dataframe(pd.DataFrame(model['forecasts']),width='stretch',hide_index=True)
            st.json(item,expanded=False)
