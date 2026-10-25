"""Minimal landing page backed only by an explicitly published example."""
import json
import streamlit as st
import pandas as pd
from .visual_theme import hero
from .dashboard_charts import candle_figure

def start_research():
    st.session_state['workspace']='研究对话'
    st.session_state['advanced_workspace']='不打开'

def render(settings):
    hero('公开研究案例','本案例聚焦 AI、机器人与前沿科技，展示行业筛选、财务核验与股票对比。产品研究范围不限于这些行业。','ZH · RESEARCH EXAMPLE')
    left,right=st.columns([3,1])
    with left: st.write('从一个问题出发，让 Agent 完成资料检索、公司比较与风险复核。')
    with right: st.button('开始研究 →',on_click=start_research,type='primary',use_container_width=True)
    path=settings.root/'examples/technology-research.json'
    if not path.exists():
        st.info('公开研究示例尚未生成。可直接开始研究，报告只保存在本机。')
    else:
        demo=json.loads(path.read_text(encoding='utf-8'))
        st.subheader('科技价值观察')
        st.caption(f"研究截止 {demo['cutoff']} · {demo['candidate_count']} 只候选 / {demo['eligible_count']} 只通过规则 · 历史快照，非实时行情")
        brief,compare,method=st.tabs(['研究判断','公司比较','研究方法'])
        with brief:
            st.markdown(demo['review'])
            st.caption('优先研究对象限于本次候选池；不代表全市场最优，也不构成收益承诺。')
        with compare:
            rows=demo['companies']
            display=pd.DataFrame([{k:r.get(k) for k in ['公司','代码','财报期间','行情日期','PE(TTM)','营收同比(%)','经营现金流(亿元)','风格评分']} for r in rows])
            st.dataframe(display,hide_index=True,use_container_width=True)
            st.caption('评分是筛选线索；不同子行业的估值不可直接比较。未取得字段留空。')
            available=[r for r in rows if r.get('bars')]
            if available:
                names=[r['公司'] for r in available]
                name=st.selectbox('查看价格与成交量',names)
                item=next(r for r in available if r['公司']==name)
                st.plotly_chart(candle_figure(pd.DataFrame(item['bars']),60,True),width='stretch',theme=None,
                    config={'displaylogo':False,'displayModeBar':False,'scrollZoom':False})
                st.caption('来源：'+item['price_source']+' · '+item['adjustment']+' · 日线快照，不代表投资策略收益')
        with method:
            st.markdown('**发现 → 验证 → 比较 → 复核**\n\n先用行业分类发现候选，再核验实际业务；财报、公告和行情共同进入证据库。基本面、技术面与消息面分别分析，最后通过乐观与审慎情景复核。')
            st.write(demo['method'])
            for limitation in demo['limitations']: st.caption('• '+limitation)
            st.markdown('[财务披露：巨潮资讯](https://www.cninfo.com.cn/) · [公开数据接口说明：AKShare](https://akshare.akfamily.xyz/data/stock/stock.html)')
    with st.expander('第一次使用'):
        st.markdown('1. 在「研究」选择投资偏好，输入行业或股票问题。\n2. 等待 Agent 获取数据并完成研究。\n3. 点击正文来源核验事实，用追问检验你的假设。')
        st.caption('新增研究需要配置模型凭证。浏览示例、切换图表不会调用模型。历史记录与专业功能在左侧「更多工具」。')
    if path.exists():
        from .report_workspace import export_controls
        report=demo['review']+'\n\n## 研究方法\n\n'+str(demo['method'])+'\n\n## 覆盖限制\n\n'+'\n'.join('- '+str(x) for x in demo['limitations'])
        columns=['公司','财报期间','PE(TTM)','营收同比(%)','经营现金流(亿元)']
        table='| '+' | '.join(columns)+' |\n| '+' | '.join('---' for _ in columns)+' |\n'
        for row in demo['companies']:
            table+='| '+' | '.join(str(row.get(k) if row.get(k) is not None else '未取得').replace('|','／') for k in columns)+' |\n'
        report+='\n\n## 公司比较\n\n'+table
        visuals={'finances':[],'models':[],'warnings':[], 'candles':[
            {'label':r['公司'],'frame':pd.DataFrame(r['bars']),'meta':{'source':r['price_source'],'adjustment':r['adjustment'],'end':r['行情日期']}}
            for r in demo['companies'] if r.get('bars')]}
        st.divider()
        export_controls({'question':'AI 与机器人 · 科技价值观察','created_at':demo['cutoff'],'report':report,'trace':[]},settings.root,key='example-export',visual_data=visuals)
