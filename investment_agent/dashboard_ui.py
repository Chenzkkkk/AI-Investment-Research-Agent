"""Read-only visual workspace over saved research, shared with report pages."""
from html import escape
import json
import re
from pathlib import Path

import pandas as pd
import streamlit as st

from .dashboard_charts import (research_rows,load_bars,candle_figure,normalized_prices,
    performance_figure,factor_figure,financial_figure,risk_figure,finite)
from .report_presentation import RUN_PATTERN,load_packet,review_text
from .visual_theme import hero,section,kpis

PLOT_CONFIG={'displaylogo':False,'scrollZoom':False,'modeBarButtonsToRemove':['lasso2d','select2d'],
             'toImageButtonOptions':{'format':'png','scale':2}}


def plot(fig,key):
    st.plotly_chart(fig,width='stretch',theme=None,config=PLOT_CONFIG,key=key)


def fmt(value,suffix='',digits=2):
    number=finite(value)
    return '—' if number is None else f'{number:,.{digits}f}{suffix}'


def saved_reports(root):
    """Only local, well-formed run directories; never load credentials or external paths."""
    records=[]
    for path in (root/'data/ashare/runs').glob('*/result.json'):
        if not re.fullmatch(RUN_PATTERN,path.parent.name): continue
        try:
            packet=load_packet(root,path.parent.name)
            result=json.loads(path.read_text(encoding='utf-8'))
            if not packet or not result.get('reports'): continue
            records.append({'run_id':path.parent.name,'topic':packet['topic'],'cutoff':packet['cutoff'],
                'status':result['status'],'created_at':result.get('created_at',''),'modified':path.stat().st_mtime})
        except (OSError,ValueError,KeyError): continue
    # Prefer latest research cutoff, then completed runs, then revision time.
    return sorted(records,key=lambda r:(r['cutoff'],r['status']=='completed',r['modified']),reverse=True)


def render_visuals(packet,root,key='dashboard'):
    rows=research_rows(packet); audit=packet.get('screening',{})
    frames,metadata,errors=load_bars(root,packet)
    available=sum(r.get('period') is not None for r in rows)
    kpis([
        ('行业候选',audit.get('candidate_count',len(rows)),'本次来源覆盖范围','radar'),
        ('财务覆盖',audit.get('financial_coverage',available),'取得结构化财务记录','layers'),
        ('通过筛选',audit.get('eligible_count','—'),'规则过滤 · 非收益预测','check'),
        ('深度研究',len(rows),f'{len(frames)} 只具备可绘制日线','focus'),
    ])
    if not rows:
        st.info('当前研究没有进入深度分析的股票。可在筛选记录中查看排除原因。'); return
    names={r['code']:r['name'] for r in rows}
    left,right=st.columns([2.15,1],gap='medium')
    with left,st.container(border=True):
        section('行情透视','PRICE / VOLUME','pulse')
        a,b,c=st.columns([1.25,1,.85])
        with a: code=st.selectbox('查看股票',list(names),format_func=lambda s:names[s]+' · '+s[:6],key=key+'-stock')
        with b: window=st.selectbox('日线窗口',[60,90,180,0],index=1,format_func=lambda n:f'最近{n}条' if n else '全部快照',key=key+'-window')
        with c: averages=st.checkbox('显示均线',value=True,key=key+'-ma')
        row=next(r for r in rows if r['code']==code)
        trend=row['return20']; color='zh-up' if trend is not None and trend>=0 else 'zh-down'
        st.markdown(f'<div class="zh-quote"><span class="zh-price">{fmt(row["close"])} <small style="font-size:13px;color:#8fa4bc">CNY</small></span><span class="{color}">20日 {fmt(trend,"%")}</span><span class="zh-meta">{escape(row["as_of"] or "日期未取得")} · 日线快照</span></div>',unsafe_allow_html=True)
        if code in frames:
            plot(candle_figure(frames[code],window,averages),key+'-candles')
            meta=metadata[code]; adj={'raw':'不复权','forward':'前复权','backward':'后复权'}.get(meta['adjustment'],meta['adjustment'])
            source={'tencent_public_kline':'腾讯公开日线','eastmoney_public_kline':'东方财富公开日线','baostock':'BaoStock'}.get(meta['source'],meta['source'])
            st.caption(f"来源 {source} · {adj} · 红涨绿跌（收盘对开盘） · 价格：元 / 成交量：万股。拖拽缩放，双击复原。")
            with st.expander('日线明细与下载'):
                data=frames[code].tail(window) if window else frames[code]
                display=data[['date','open','high','low','close','volume']].rename(columns={'date':'日期','open':'开盘','high':'最高','low':'最低','close':'收盘','volume':'成交量（股）'})
                st.dataframe(display,hide_index=True,use_container_width=True)
                st.download_button('下载当前日线 CSV',display.to_csv(index=False).encode('utf-8-sig'),
                    file_name=f'{code}-{packet["cutoff"]}-日线.csv',key=key+'-bars-csv')
        else: st.info(errors.get(code,'该股票未取得可绘制的历史日线，未使用模拟曲线。'))
    with right,st.container(border=True):
        section('研究画像','FACTOR MAP','radar')
        st.caption(audit.get('profile',{}).get('label','指定股票研究')+' · 候选风格匹配')
        heat=factor_figure(rows,audit.get('profile',{}).get('weights'))
        if heat is not None:
            plot(heat,key+'-factors')
            st.caption('分位评分 0—100；质量、成长、估值在子行业内比较。未参与本风格或未取得的维度留空，不按零计算。')
        else: st.info('指定个股研究没有行业评分，不编造雷达分数。')
        section('快照状态','EVIDENCE','layers')
        dates=sorted({str(r['as_of']) for r in rows if r['as_of']})
        st.caption('行情日期：'+' / '.join(dates) if dates else '行情日期未取得')
        st.caption('研究截止：'+packet['cutoff']+'；历史研究不会自动变成实时数据。')
        st.caption('均线由完整快照计算后截取窗口；不插值补齐缺失日线。')
        if errors:
            with st.expander('部分日线不可用'):
                for code,message in errors.items(): st.write(names[code]+'：'+message)
    section('股票对比','COMPARE / INVESTIGATE','chart')
    selected=st.multiselect('选择对比股票',list(names),default=list(names),format_func=lambda c:names[c],key=key+'-compare')
    if not selected:
        st.info('至少选择一只股票查看对比。'); return
    chosen=[r for r in rows if r['code'] in selected]
    tabs=st.tabs(['走势对比','盈利与现金流','收益与波动','关键指标'])
    with tabs[0],st.container(border=True):
        comparable={c:frames[c] for c in selected if c in frames}
        adjustments={metadata[c]['adjustment'] for c in comparable}
        indexed=normalized_prices(comparable,window) if len(adjustments)<=1 else pd.DataFrame()
        if not indexed.empty:
            plot(performance_figure(indexed,names),key+'-performance')
            st.caption(f'共同观测区间 {indexed.index[0]} — {indexed.index[-1]}，起点统一为100。只使用各股均有记录的日期，不补值；为不含分红的价格变化，非策略业绩。')
        else: st.info('复权口径不一致，暂不混合比较。' if len(adjustments)>1 else '共同日期不足，暂无法比较走势。')
        absent=[names[c] for c in selected if c not in comparable]
        if absent: st.caption('未加入曲线：'+'、'.join(absent)+'（日线未取得）')
    with tabs[1],st.container(border=True):
        fig=financial_figure(chosen)
        if fig is not None:
            plot(fig,key+'-cash'); st.caption('报告期：'+next(r['period'] for r in chosen if r['period'])+'，单位：亿元。现金流为合并口径，不能直接等同归母利润现金含量；未将半年数据年化。')
        else: st.info('财报期间不同或未取得，暂不绘制并列比较；请查看关键指标中的报告期。')
        absent=[r['name'] for r in chosen if not r['period']]
        if absent: st.caption('未加入财务图：'+'、'.join(absent)+'（财报未取得）')
    with tabs[2],st.container(border=True):
        fig=risk_figure(chosen)
        if fig is not None:
            plot(fig,key+'-risk'); st.caption('过去20日收益与过去20日日收益率的年化波动对比，非未来收益或风险预测；不同股票行情日期见关键指标。')
        else: st.info('缺少可比较的收益与波动指标。')
    with tabs[3]:
        table=pd.DataFrame([{'股票':r['name'],'代码':r['code'],'行情日期':r['as_of'] or '未取得','财报期间':r['period'] or '未取得',
            '收盘价（元）':fmt(r['close']),'20日收益':fmt(r['return20'],'%'),'60日回撤':fmt(r['drawdown'],'%'),
            'PE（TTM）':fmt(r['pe']),'ROE':fmt(r['roe'],'%'),'营收同比':fmt(r['growth'],'%'),
            '风格评分':fmt(r['score'],digits=1)} for r in chosen])
        st.dataframe(table,hide_index=True,use_container_width=True)
        st.download_button('下载对比指标 CSV',table.to_csv(index=False).encode('utf-8-sig'),file_name='股票对比.csv',key=key+'-compare-csv')
        st.caption('— 表示未取得，不按零处理。PE、ROE和成长性须结合行业、报告期及重组情况判断。')


def go_chat():
    st.session_state['workspace']='研究对话'
    st.session_state['advanced_workspace']='不打开'


def render_home(settings):
    from .discover_ui import render
    render(settings)


def render_history(settings):
    hero('把研究，变成可见的判断。','从行业扫描到个股证据，用同一张看板观察价格、财务与风险。','A-SHARE / RESEARCH OBSERVATORY')
    records=saved_reports(settings.root)
    if not records:
        st.markdown('<div class="zh-empty">尚无已保存研究。完成一次研究后，真实K线、股票对比与财务图表会出现在这里。</div>',unsafe_allow_html=True)
        st.button('发起第一份研究',type='primary',on_click=go_chat); return
    options={r['run_id']:r for r in records}
    a,b,c=st.columns([3,1,1])
    with a:
        run_id=st.selectbox('研究快照',list(options),format_func=lambda r:options[r]['topic']+' · '+options[r]['cutoff']+' · '+r[-8:],key='dashboard-saved')
    with b:
        st.caption('阅读完整判断')
        st.link_button('打开研究报告 ↗','?research='+run_id,use_container_width=True)
    with c:
        st.caption('用新的问题开始')
        st.button('＋ 发起新研究',type='primary',on_click=go_chat,use_container_width=True)
    packet=load_packet(settings.root,run_id)
    result=json.loads((settings.root/'data/ashare/runs'/run_id/'result.json').read_text(encoding='utf-8'))
    st.caption(f"保存的研究 · 截止 {packet['cutoff']} · {packet.get('data_mode','来源见报告')} · {'已完成' if result['status']=='completed' else '部分完成'} · 非实时行情")
    render_visuals(packet,settings.root,key='home-'+run_id)
    with st.expander('阅读 Agent 的结论与投资条件'):
        st.markdown(review_text(result.get('reports',{}).get('review','最终建议尚未完成。'),packet))
    st.caption('图表只重绘已有证据；切换股票和窗口不会发起模型或数据API调用。')
