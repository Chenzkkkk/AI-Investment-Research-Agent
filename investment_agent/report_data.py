"""Read-only report projections. Never fetch fresh data while viewing a snapshot."""
import json
import re
import ipaddress
from urllib.parse import urlsplit
from pathlib import Path
import pandas as pd


def summary(markdown, limit=230):
    # Prefer the actual conclusion to timestamps or editorial preambles in old reports.
    match=re.search(r'(?m)^#{1,4}\s*(?:结论(?:与建议)?|主要判断|核心结论|研究摘要)[^\n]*\n+',str(markdown))
    if match: markdown=str(markdown)[match.end():]
    paragraphs=[p.strip() for p in str(markdown).split('\n\n') if p.strip() and not p.lstrip().startswith(('#','|','---'))]
    text=paragraphs[0] if paragraphs else str(markdown).strip()
    text=re.sub(r'\[([^\]]+)\]\([^)]*\)',r'\1',text)
    text=re.sub(r'[*`#]', '', text).replace('\n',' ')
    return text[:limit]+('…' if len(text)>limit else '')


def title(result):
    explicit=result.get('report_title')
    heading=re.search(r'(?m)^#\s+(.+)',result.get('report',''))
    text=str(explicit or (heading[1] if heading else result.get('question')) or '投资研究报告')
    if not explicit and not heading and len(text)>55:
        for entry in result.get('trace',[]):
            data=(entry.get('result') or {}).get('data')
            if entry.get('tool')=='financial_model' and isinstance(data,dict) and data.get('name'):
                return str(data['name'])+' · 公司研究报告'
    return text if len(text)<=55 else text[:52]+'…'


def external_url(value):
    from .report_presentation import public_url
    url=public_url(value)
    if not url: return None
    host=urlsplit(url).hostname or ''
    if host.lower() in ('localhost','localhost.localdomain') or host.lower().endswith(('.local','.localhost')): return None
    try:
        if not ipaddress.ip_address(host).is_global: return None
    except ValueError: pass
    return url


def report_id(result):
    name=Path(result.get('report_file') or '').stem
    return name if re.fullmatch('[a-f0-9]{32}',name) else None


def catalog(root):
    records=[]
    for p in (Path(root)/'data/reports').glob('*.json'):
        if not re.fullmatch('[a-f0-9]{32}',p.stem): continue
        try:
            d=json.loads(p.read_text(encoding='utf-8'))
            if not d.get('report'): continue
            records.append({'id':p.stem,'kind':'report','title':d.get('question') or '研究报告',
                'date':d.get('created_at',''),'summary':summary(d['report']), 'modified':p.stat().st_mtime})
        except (OSError,ValueError,TypeError): continue
    from .dashboard_ui import saved_reports
    for d in saved_reports(Path(root)):
        records.append({'id':d['run_id'],'kind':'research','title':d['topic'],'date':d.get('created_at') or d['cutoff'],
                        'summary':'行业筛选、股票对比、分析依据与历史行情。','modified':d['modified']})
    return sorted(records,key=lambda d:d['modified'],reverse=True)


def projections(result, root):
    """Only known tool contracts are interpreted; all original evidence remains intact."""
    finances=[]; candles=[]; models=[]; valuations=[]; warnings=[]; seen=set(); joint=[]
    for entry in result.get('trace',[]):
        data=(entry.get('result') or {}).get('data')
        if not isinstance(data,dict): continue
        if entry.get('tool') in ('joint_stock_research','read_joint_research') and data.get('run_id'):
            from .report_presentation import load_packet
            packet=load_packet(Path(root),data['run_id'])
            if packet and packet['run_id'] not in {p['run_id'] for p in joint}:
                joint.append(packet)
                from .dashboard_charts import load_bars
                frames,metas,errors=load_bars(Path(root),packet)
                warnings.extend(errors.values())
                for code,frame in frames.items():
                    if code in seen: continue
                    seen.add(code)
                    name=next((x['name'] for x in packet['universe'] if x['code']==code),code)
                    candles.append({'label':name,'frame':frame,'meta':metas[code]})
                for item in packet.get('evidence',[]):
                    if item.get('kind')=='fundamental' and item.get('data',{}).get('periods'):
                        finances.append({'label':item['subject'],'rows':item['data']['periods'],'id':item['id'],'coverage_note':'联合研究保存的财报期间，原始口径见证据。'})
        skill=data.get('skill')
        payload=data.get('data') if skill else data
        if not isinstance(payload,dict): continue
        if skill=='valuation':
            valuations.append({'id':entry.get('id',''),'data':payload,'assumptions':data.get('assumptions',{}),
                'sources':data.get('sources',[]),'label':'估值计算 · '+str(entry.get('id',''))})
        if entry.get('tool')=='financial_model' or skill=='financial-quality':
            rows=payload.get('analysis',{}).get('rows',[])
            if rows:
                finances.append({'label':payload.get('name') or payload.get('symbol') or entry.get('id','财务'),
                    'rows':rows,'id':entry.get('id'),'as_of':payload.get('as_of'),
                    'coverage_note':payload.get('coverage_note',''),'dcf':payload.get('dcf'),
                    'quality':payload.get('quality',{}),'documents':payload.get('document_analysis',[])})
            if payload.get('dcf'):
                valuations.append({'id':entry.get('id',''),'data':payload['dcf'],'assumptions':{},
                    'sources':[],'label':str(payload.get('name') or '公司')+' · FCFF 情景'})
        if payload.get('model')=='linked-three-statement':
            models.append(payload)
        if skill=='technical-analysis' or entry.get('tool')=='load_history':
            snapshot=((payload.get('snapshot') or {}).get('data') or {}) if skill else payload
            sid=snapshot.get('id')
            if sid and sid not in seen:
                seen.add(sid)
                try:
                    from .quant_data import DatasetStore
                    frame,meta=DatasetStore(root).load(sid)
                    if meta.get('synthetic'): raise ValueError('Synthetic snapshot')
                    if snapshot.get('sha256') and snapshot['sha256']!=meta.get('sha256'): raise ValueError('Snapshot hash mismatch')
                    cutoff=str(result.get('created_at') or '')[:10]
                    if re.fullmatch(r'\d{4}-\d{2}-\d{2}',cutoff): frame=frame.loc[frame.date<=cutoff]
                    if frame.empty: raise ValueError('No data before research time')
                    meta={**meta,'end':str(frame.date.max())}
                    # A dataset can hold several stocks. Never mix their candle series.
                    for symbol,part in frame.groupby('symbol') if 'symbol' in frame else [(str(meta.get('symbols','股票')),frame)]:
                        candles.append({'label':str(symbol),'frame':part.sort_values('date'),'meta':meta})
                except Exception:
                    warnings.append(f'行情快照 {sid} 无法读取或完整性校验失败；未绘制替代数据。')
    # Revaluing the same saved statements must not create duplicate company charts.
    unique_finances={}
    for item in finances:
        signature=json.dumps([item['label'],item.get('as_of'),item['rows']],sort_keys=True,ensure_ascii=False,default=str)
        if signature in unique_finances:
            previous=unique_finances[signature]
            for field in ('dcf','quality','documents'):
                if item.get(field): previous[field]=item[field]
        else: unique_finances[signature]=item
    return {'finances':list(unique_finances.values()),'candles':candles,'models':models,'valuations':valuations,'warnings':warnings,'joint':joint}


def financial_frame(item):
    return pd.DataFrame([{'报告期':r['period'],'营业收入':r['values'].get('revenue'),
        '经营现金流':r['values'].get('operating_cashflow'),'归母净利润':r['values'].get('profit_parent')}
        for r in item['rows']]).sort_values('报告期')


def source_links(result):
    """Published links only, deduplicated. Do not export arbitrary tool credentials."""
    links={}
    def walk(value):
        if isinstance(value,dict):
            url=external_url(value.get('url'))
            if url: links.setdefault(url,str(value.get('title') or value.get('name') or '原始材料'))
            for k,v in value.items():
                if k not in ('body','raw','content','requests'): walk(v)
        elif isinstance(value,list):
            for v in value: walk(v)
    walk(result.get('trace',[]))
    for label,url in re.findall(r'\[([^\]]+)\]\((https?://[^\s)]+)\)',result.get('report','')):
        if external_url(url): links[url]=label
    return [{'title':label,'url':url} for url,label in links.items()]
