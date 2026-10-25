"""Presentation-only charts from verified research snapshots. No network calls."""
from pathlib import Path
import math

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from .common import DataError
from .quant_data import DatasetStore

COLORS = ['#59d6d0','#72a7ff','#e8b96b','#b49aff','#f18ca9']
UP, DOWN = '#f47582', '#45c5a1'  # A-share convention: red up, green down.
BG, GRID, TEXT = '#111c2c', '#253247', '#b8c9dc'


def finite(value):
    try:
        value=float(value)
        return value if math.isfinite(value) else None
    except (TypeError,ValueError): return None


def style(fig,height=360):
    fig.update_layout(template='plotly_dark',paper_bgcolor=BG,plot_bgcolor=BG,
        font=dict(family='Segoe UI, Microsoft YaHei, sans-serif',color=TEXT,size=12),
        height=height,margin=dict(l=20,r=22,t=30,b=30),
        legend=dict(orientation='h',y=1.12,x=0,font=dict(size=11)),
        hoverlabel=dict(bgcolor='#182b40',font_size=12),dragmode='zoom')
    fig.update_xaxes(gridcolor=GRID,zeroline=False,showgrid=False)
    fig.update_yaxes(gridcolor=GRID,zerolinecolor=GRID)
    return fig


def research_rows(packet):
    screen={r['code']:r for r in packet.get('screening',{}).get('rows',[])}
    rows=[]
    for asset in packet.get('universe',[]):
        tech=next((e['data'] for e in packet.get('evidence',[]) if e['kind']=='technical' and e['subject']==asset['name']),{})
        fund=next((e['data'] for e in packet.get('evidence',[]) if e['kind']=='fundamental' and e['subject']==asset['name']),{})
        periods=[p for p in fund.get('periods',[]) if p['period']<=packet['cutoff']]
        period=max(periods,key=lambda p:p['period']) if periods else {}
        s=screen.get(asset['code'],{}); values=period.get('values',{}); derived=period.get('derived',{})
        rows.append({'code':asset['code'],'name':asset['name'],'segment':asset.get('segment',''),'color':COLORS[len(rows)%5],
            'close':finite(tech.get('close')),'as_of':tech.get('as_of'),
            'return20':finite(tech.get('return20_pct')),'volatility':finite(tech.get('volatility20_annual_pct')),
            'drawdown':finite(tech.get('drawdown60_pct')),'score':finite(s.get('score')),
            'pe':finite(s.get('pe_ttm',tech.get('peTTM'))),'roe':finite(values.get('roe_pct')),
            'revenue':finite(values.get('revenue')),'profit':finite(values.get('profit_parent')),
            'cash':finite(values.get('operating_cashflow')),'growth':finite(derived.get('revenue_yoy_pct')),
            'period':period.get('period'),'components':s.get('components',{}),'technical':tech})
    return rows


def load_bars(root: Path,packet: dict):
    """Verify snapshot hash/identity and cutoff; never substitute a newer dataset."""
    frames={}; metadata={}; errors={}
    for asset in packet.get('universe',[]):
        item=next((e for e in packet.get('evidence',[]) if e['kind']=='technical' and e['subject']==asset['name']),None)
        data=item.get('data',{}) if item else {}
        if not data.get('dataset_id'):
            errors[asset['code']]='本次研究没有可用的日线快照'; continue
        try:
            frame,meta=DatasetStore(root).load(data['dataset_id'])
            if meta.get('synthetic'): raise DataError('CHART_SYNTHETIC','研究看板不展示合成行情')
            if set(frame.symbol)!={asset['code']}: raise DataError('CHART_SYMBOL','行情快照的证券代码与报告不符')
            if data.get('dataset_sha256') and data['dataset_sha256']!=meta['sha256']:
                raise DataError('CHART_HASH','报告与行情快照校验值不一致')
            if data.get('adjustment') and data['adjustment']!=meta['adjustment']:
                raise DataError('CHART_ADJUSTMENT','报告与快照复权方式不一致')
            end=min(packet['cutoff'],data.get('as_of') or packet['cutoff'])
            frame=frame.loc[frame.date<=end].sort_values('date').copy()
            if frame.empty: raise DataError('CHART_EMPTY','截止日前没有日线')
            frames[asset['code']]=frame; metadata[asset['code']]=meta
        except (DataError,OSError,ValueError,KeyError) as exc:
            errors[asset['code']]=str(exc) if isinstance(exc,DataError) else '快照读取失败，未用其他行情替代'
    return frames,metadata,errors


def candle_figure(frame,window=90,averages=True):
    full=frame.sort_values('date').copy()
    for period in (5,20,60): full[f'ma{period}']=full.close.rolling(period,min_periods=period).mean()
    data=full.tail(window) if window else full
    fig=make_subplots(rows=2,cols=1,shared_xaxes=True,vertical_spacing=.035,row_heights=[.77,.23])
    fig.add_trace(go.Candlestick(x=data.date.tolist(),open=data.open.tolist(),high=data.high.tolist(),
        low=data.low.tolist(),close=data.close.tolist(),name='日K',
        increasing=dict(line=dict(color=UP,width=1),fillcolor=UP),
        decreasing=dict(line=dict(color=DOWN,width=1),fillcolor=DOWN)),row=1,col=1)
    if averages:
        for period,color in zip((5,20,60),('#e8b96b','#72a7ff','#b49aff')):
            fig.add_trace(go.Scatter(x=data.date.tolist(),y=data[f'ma{period}'].tolist(),name=f'MA{period}',
                mode='lines',line=dict(color=color,width=1.3),connectgaps=False,
                hovertemplate=f'MA{period}: %{{y:.2f}}元<extra></extra>'),row=1,col=1)
    fig.add_trace(go.Bar(x=data.date.tolist(),y=(data.volume/10000).tolist(),name='成交量',
        marker_color=[UP if c>=o else DOWN for c,o in zip(data.close,data.open)],opacity=.7,
        hovertemplate='%{x}<br>成交量 %{y:,.2f} 万股<extra></extra>'),row=2,col=1)
    style(fig,450)
    fig.update_layout(xaxis_rangeslider_visible=False,showlegend=True,hovermode='x unified',
        legend=dict(y=1.13),margin=dict(l=12,r=12,t=42,b=20))
    # Show only observations as trading sessions, without interpolated holiday bars.
    fig.update_xaxes(type='category',categoryorder='array',categoryarray=data.date.tolist(),nticks=7,
        showspikes=True,spikecolor='#59d6d0',spikemode='across')
    fig.update_yaxes(title_text='价格 / 元',row=1,col=1)
    fig.update_yaxes(title_text='万股',row=2,col=1)
    return fig


def normalized_prices(frames,window=90):
    """Same start/date intersection for every selected stock; no forward filling."""
    if not frames: return pd.DataFrame()
    series=[f.set_index('date').close.rename(code) for code,f in frames.items()]
    joined=pd.concat(series,axis=1,join='inner').sort_index().dropna()
    if window: joined=joined.tail(window)
    if len(joined)<2: return pd.DataFrame()
    return joined.divide(joined.iloc[0]).multiply(100)


def performance_figure(indexed,names):
    fig=go.Figure()
    for i,code in enumerate(indexed.columns):
        fig.add_trace(go.Scatter(x=indexed.index.tolist(),y=indexed[code].tolist(),name=names[code],
            mode='lines',line=dict(color=COLORS[list(names).index(code)%5],width=2),connectgaps=False,
            hovertemplate='%{x}<br>同起点指数 %{y:.2f}<extra>%{fullData.name}</extra>'))
    style(fig,360); fig.add_hline(y=100,line_dash='dot',line_color='#50627a')
    fig.update_layout(hovermode='x unified'); fig.update_yaxes(title_text='起点 = 100')
    return fig


def factor_figure(rows,weights=None):
    usable=[r for r in rows if r.get('components')]
    if not usable: return None
    labels=['质量','成长','估值','趋势','流动性']; keys=['quality','growth','value','trend','liquidity']
    z=[[None if weights is not None and weights.get(k)==0 else finite(r['components'].get(k)) for k in keys] for r in usable]
    fig=go.Figure(go.Heatmap(z=z,x=labels,y=[r['name'] for r in usable],zmin=0,zmax=100,
        colorscale=[[0,'#17293d'],[.5,'#28646f'],[1,'#59d6d0']],showscale=False,
        texttemplate='%{z:.0f}',textfont=dict(color='#e7f2f8',size=12),xgap=5,ygap=5,
        hovertemplate='%{y} · %{x}<br>分位评分 %{z:.1f}/100<extra></extra>'))
    style(fig,310); fig.update_yaxes(autorange='reversed',showgrid=False); fig.update_xaxes(side='top')
    return fig


def financial_figure(rows):
    available=[r for r in rows if r['period']]
    periods={r['period'] for r in available}
    if not available or len(periods)!=1: return None
    fig=go.Figure()
    for field,label,color in [('profit','归母净利润',COLORS[1]),('cash','经营现金流',COLORS[0])]:
        fig.add_trace(go.Bar(name=label,x=[r['name'] for r in available],
            y=[r[field]/1e8 if r[field] is not None else None for r in available],marker_color=color,
            hovertemplate='%{x}<br>%{y:.2f} 亿元<extra>%{fullData.name}</extra>'))
    style(fig,350); fig.update_layout(barmode='group'); fig.update_yaxes(title_text='亿元')
    return fig


def risk_figure(rows):
    valid=[r for r in rows if r['volatility'] is not None and r['return20'] is not None]
    if not valid: return None
    fig=go.Figure()
    for i,r in enumerate(valid):
        fig.add_trace(go.Scatter(x=[r['volatility']],y=[r['return20']],mode='markers+text',name=r['name'],
            text=[r['name']],textposition='top center',marker=dict(size=14,color=r.get('color',COLORS[i%5]),line=dict(width=2,color=BG)),
            hovertemplate='%{text}<br>20日年化波动 %{x:.2f}%<br>20日价格收益 %{y:.2f}%<extra></extra>'))
    style(fig,350); fig.update_layout(showlegend=False,margin=dict(l=20,r=55,t=40,b=35))
    fig.add_hline(y=0,line_dash='dot',line_color='#50627a')
    fig.update_xaxes(title_text='20日年化波动率 / %',rangemode='tozero')
    fig.update_yaxes(title_text='20日价格收益 / %')
    return fig
