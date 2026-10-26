"""Reader-facing provenance, separate from raw tool telemetry."""
import re
from urllib.parse import urlsplit, parse_qs
from .report_presentation import public_url, markdown_link


def readable_url(value):
    url=public_url(value)
    if not url: return None
    p=urlsplit(url); host=p.hostname or ''
    if host in ('localhost','127.0.0.1','::1'): return None
    if host in ('beian.miit.gov.cn','beian.mps.gov.cn','www.beian.gov.cn'): return None
    if re.search(r'(^|\.)(?:api|datacenter|datacenter-web|push2|push2his|np-anotice)(\.|$)',host): return None
    if re.search(r'\.(?:json|csv)(?:$|\?)|/(?:api|v\d+/api)/|ajax|/get[A-Z]|/api\.',p.path,re.I): return None
    return url


def originals(value):
    found={}
    def walk(x,parent_title=''):
        if isinstance(x,dict):
            title=str(x.get('title') or x.get('label') or x.get('name') or parent_title or '原始资料')
            for key in ('url','source_url'):
                url=readable_url(x.get(key))
                if url:
                    label=title
                    if x.get('period'): label+=' · '+str(x['period'])
                    if x.get('page'): label+=' · 第'+str(x['page'])+'页'
                    if url not in found or found[url]=='原始资料' or x.get('title'): found[url]=label
            for k,v in x.items():
                if k not in ('raw','body','content','requests','instructions','method','china_method_sources'):
                    if k=='sources' and isinstance(v,list):
                        for u in v:
                            if isinstance(u,str) and readable_url(u): found.setdefault(readable_url(u),title+' · 来源')
                    walk(v,title)
        elif isinstance(x,list):
            for v in x: walk(v,parent_title)
    walk(value)
    return [{'title':label,'url':url} for url,label in found.items()]


def source_appendix(trace):
    links=originals([e for e in trace if e.get('result',{}).get('source') not in ('research_method','coverage_audit')])
    return '\n\n## 原文与数据依据\n\n'+('\n'.join('- '+markdown_link(x['title'],x['url']) for x in links) if links else
        '本轮未保存可直接阅读的原文网页。数据看板保留输入、期间和计算结果；不能据此宣称已核验原文。')


def reader_report(result,root):
    """Presentation migration only; immutable archived raw report stays on disk."""
    from .report_presentation import chat_links, linked_text
    text=result.get('report',''); links=chat_links(result,root)
    text=re.split(r'\n---\s*\n数据证据\s*\n',text)[0]
    def replace(m):
        label,url=m[1],m[2]; q=parse_qs(urlsplit(url).query)
        sid=(q.get('source') or [None])[0]
        if sid in links: return markdown_link(*links[sid])
        if re.fullmatch(r'S\d+',label) and label in links: return markdown_link(*links[label])
        if not readable_url(url) and urlsplit(url).hostname not in ('127.0.0.1','localhost'):
            return label+'（数据接口，详见数据看板）'
        return m[0]
    text=re.sub(r'\[([^\]]+)\]\((https?://[^\s)]+)\)',replace,text)
    text=linked_text(text,links)
    # Keep limitations, translate developer states; never upgrade a historical conclusion.
    if 'not_run' in text:
        from .professional_skills import CoverageChecklist
        c=CoverageChecklist()
        for item in result.get('research_coverage',{}).get('items',[]):
            if item.get('factor'):c.items[item['factor']]={k:v for k,v in item.items() if k!='factor'}
        c.observe(result.get('trace',[]))
        corrected=[v['label'] for v in c.items.values() if v['status']=='calculated']
        if corrected:text='> 存档说明：根据保存的执行结果，'+ '、'.join(corrected)+'已完成程序计算。旧正文中的“未记录执行状态”是覆盖记录缺漏；不代表未计算，也不代表已交叉核验全部假设。\n\n'+text
    text=text.replace('`not_run`','“未记录执行状态”').replace('not_run','未记录执行状态')
    return text if '## 原文与数据依据' in text else text+source_appendix(result.get('trace',[]))


def render_evidence(item):
    import streamlit as st
    import pandas as pd
    result=item.get('result',{}); data=result.get('data') or {}
    st.markdown('## 依据与计算说明')
    sources=originals(result)
    for source in sources[:6]: st.link_button(source['title'],source['url'])
    if len(sources)>6:
        with st.expander('更多原文与具体页码（'+str(len(sources)-6)+'项）'):
            for source in sources[6:]: st.link_button(source['title'],source['url'])
    if not sources: st.info('本记录没有可阅读的原文链接；不能将数据接口当成财报原文。')
    if isinstance(data,dict) and data.get('skill'): data=data.get('data',{})
    if isinstance(data,dict) and data.get('analysis',{}).get('rows'):
        from .report_data import financial_frame
        st.caption('金额单位：元；下表为本次保存的报告期，不是实时财报。')
        st.dataframe(financial_frame({'rows':data['analysis']['rows']}),hide_index=True,width='stretch')
        for f in data.get('quality',{}).get('findings',[]): st.write(f if isinstance(f,str) else f.get('message',str(f)))
    if isinstance(data,dict) and data.get('dcf'):
        from .valuation_views import valuation_markdown
        st.markdown(valuation_markdown({'label':'现金流估值与假设','data':data['dcf']}))
    elif isinstance(data,dict) and (data.get('scenarios') or data.get('model') in ('FCFF-DCF','equity_bridge') or 'value_per_share' in data):
        from .valuation_views import valuation_markdown
        st.markdown(valuation_markdown({'label':'模型计算与假设','data':data,'assumptions':result.get('data',{}).get('assumptions',{})}))
    if isinstance(data,dict) and data.get('capital_bridge'):
        from .capital_bridge import bridge_markdown
        st.markdown(bridge_markdown(data['capital_bridge'],data.get('equity_valuation')))
    if result.get('source')=='local_intelligence':
        events=data if isinstance(data,list) else [data]
        for event in events:
            if not isinstance(event,dict): continue
            a=event.get('analysis') or {}; st.markdown('### '+str(a.get('title_zh') or event.get('title','相关事件')))
            st.write(a.get('summary_zh') or '中文摘要尚未完成，请从上方入口阅读原始资料。')
            for f in a.get('facts',[]): st.write(f.get('text',''))
            if a.get('unknowns'): st.write('尚待确认：'+'；'.join(a['unknowns']))
    elif isinstance(data,dict) and not data.get('analysis',{}).get('rows'):
        labels={'price':'价格','close':'收盘价','open':'开盘价','high':'最高价','low':'最低价','volume':'成交量',
                'amount':'成交额','date':'数据日期','as_of':'时点','name':'名称','symbol':'证券代码','formula':'计算公式',
                'total_return_pct':'区间价格收益（%）','max_drawdown_pct':'最大回撤（%）','basis':'计算口径',
                'analysis':'研究解读','instruction':'口径说明','data_time':'数据日期','model':'方法'}
        for key,label in labels.items():
            v=data.get(key)
            if isinstance(v,(str,int,float)) and not isinstance(v,bool): st.write('**'+label+'**：'+str(v))
        rows=data.get('rows') or data.get('factors')
        if isinstance(rows,list) and rows and isinstance(rows[0],dict):
            st.dataframe(pd.DataFrame(rows[:50]).rename(columns=labels),hide_index=True,width='stretch')
    with st.expander('开发者诊断（原始返回值）',expanded=False): st.json(result)
