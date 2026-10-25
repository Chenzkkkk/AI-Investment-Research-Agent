"""Evidence-first sector discovery, public financials and reproducible style scoring.

Public Eastmoney data-center feeds are website data, not a paid Choice API or
an exchange disclosure. Preserve their origin, dates, failures and raw responses.
"""
from datetime import date, timedelta
import json
import re
import math

import httpx

from .common import DataError, Result, now, number, symbol
from .ashare_metrics import FIELDS, financial_metrics, tables

PROFILES = {
    'balanced': {'label':'均衡研究', 'horizon':'3—12个月', 'weights':{'quality':.30,'growth':.25,'value':.20,'trend':.15,'liquidity':.10}},
    'short': {'label':'短线交易', 'horizon':'数日至4周', 'weights':{'quality':.15,'growth':.10,'value':.05,'trend':.45,'liquidity':.25}},
    'trend': {'label':'趋势波段', 'horizon':'1—3个月', 'weights':{'quality':.20,'growth':.15,'value':.10,'trend':.40,'liquidity':.15}},
    'value': {'label':'价值投资', 'horizon':'1—3年', 'weights':{'quality':.40,'growth':.10,'value':.40,'trend':0.,'liquidity':.10}},
    'growth': {'label':'成长投资', 'horizon':'6—24个月', 'weights':{'quality':.25,'growth':.45,'value':.15,'trend':.05,'liquidity':.10}},
    'event': {'label':'事件驱动', 'horizon':'事件兑现期', 'weights':{'quality':.25,'growth':.20,'value':.15,'trend':.20,'liquidity':.20}},
}
API = 'https://datacenter-web.eastmoney.com/api/data/v1/get'
REPORTS = ('RPT_LICO_FN_CPD','RPT_DMSK_FN_INCOME','RPT_DMSK_FN_BALANCE','RPT_DMSK_FN_CASHFLOW')
MAP = {'OPERATE_INCOME':'revenue','TOTAL_OPERATE_INCOME':'total_operating_income','PARENT_NETPROFIT':'profit_parent','DEDUCT_PARENT_NETPROFIT':'profit_ex_items',
       'NETCASH_OPERATE':'operating_cashflow','TOTAL_ASSETS':'assets','TOTAL_LIABILITIES':'liabilities',
       'ACCOUNTS_RECE':'receivables','INVENTORY':'inventory','WEIGHTAVG_ROE':'roe_pct','XSMLL':'gross_margin_pct',
       'TOTAL_PROFIT':'profit_total','INCOME_TAX':'income_tax','TOTAL_EQUITY':'equity_total',
       'CONSTRUCT_LONG_ASSET':'capex_cash','MONETARYFUNDS':'cash','OPERATE_PROFIT':'operating_profit'}
CORE = ('revenue','profit_parent','profit_ex_items','operating_cashflow','assets','liabilities','receivables','inventory','roe_pct')

def metric(row, patterns):
    for key,value in row.items():
        if any(re.search(p,str(key),re.I) for p in patterns):
            # Skill CSV values use display units; header supplies units for raw cells.
            raw=str(value).split('|')[0]
            val=number(raw)
            if val is not None:
                if not any(u in raw for u in ('亿','万')):
                    if '亿元' in key: val*=1e8
                    elif '万元' in key: val*=1e4
                return val, str(key)
    return None,''


def observation_date(label):
    values=re.findall(r'\d{4}[.-]\d{2}[.-]\d{2}',label)
    return values[-1].replace('.','-') if values else ''

def candidates(payload,topic,limit=150):
    rows=payload.get('rows',[]) if isinstance(payload,dict) else []
    if not rows:
        for table in tables(payload):
            rows.extend(dict(zip(table['columns'],r)) for r in table['items'] if isinstance(r,list))
    accepted=[]; rejected=[]; seen=set()
    for row in rows:
        code=str(row.get('代码') or row.get('股票代码') or row.get('证券代码') or '').strip()
        name=str(row.get('名称') or row.get('股票简称') or row.get('证券简称') or '').strip()
        match=re.search(r'(?<!\d)(\d{6})(?:\.(SH|SZ|BJ))?(?!\d)',code,re.I)
        if not match or not name: continue
        code=symbol(match.group(0))
        if code in seen: continue
        seen.add(code)
        industry=str(row.get('申万行业分类') or row.get('所属行业') or row.get('行业') or row.get('东财行业总分类') or '')
        reason=None
        literal_scope=re.sub(r'A股|行业|板块|研究|分析|机会|投资|建议','',topic).strip()
        thematic=any(x in topic.lower() for x in ('ai','人工智能','机器人','前沿科技'))
        if not thematic and re.fullmatch(r'[\u4e00-\u9fff]{2,8}',literal_scope) and literal_scope not in industry:
            reason='未确认属于所请求行业分类'
        if re.search('ST|退',name,re.I) or row.get('ST股票')=='是' or row.get('退市股(退市整理期)')=='是': reason='ST或退市风险排除'
        if not re.fullmatch(r'[\u4e00-\u9fffA-Za-z*]{2,15}',name): reason='证券简称格式未确认'
        if reason: rejected.append({'code':code,'name':name,'reason':reason}); continue
        accepted.append({'code':code,'name':name,'segment':industry or '供应商主题筛选，行业待核验','screen_row':row})
    return accepted[:limit],rejected

async def public_financials(service,assets,cutoff,history_days=800):
    """Chunked/paginated latest statements, bounded by publication date."""
    output={}; requests=[]
    start=(date.fromisoformat(cutoff)-timedelta(days=history_days)).isoformat()
    async with httpx.AsyncClient(timeout=30,trust_env=False) as client:
        for offset in range(0,len(assets),40):
            codes=[x['code'][:6] for x in assets[offset:offset+40]]
            code_filter=','.join('"'+c+'"' for c in codes)
            for report in REPORTS:
                period_key='REPORTDATE' if report==REPORTS[0] else 'REPORT_DATE'
                for page in range(1,5):
                    params={'reportName':report,'columns':'ALL','pageSize':500,'pageNumber':page,
                        'sortColumns':period_key+',SECURITY_CODE','sortTypes':'-1,1',
                        'filter':f'(SECURITY_CODE in ({code_filter}))(NOTICE_DATE<=\'{cutoff}\')({period_key}>=\'{start}\')'}
                    async def action(params=params):
                        response=await client.get(API,params=params)
                        response.raise_for_status(); body=response.json()
                        if not body.get('success') or not body.get('result'):
                            raise DataError('PUBLIC_FINANCIAL_EMPTY','东方财富公开财报未返回匹配记录。')
                        return Result(source='eastmoney_public',kind='financial_statements',data=body['result'],
                                      metadata={'url':str(response.url),'units':'CNY; ratios in percent'})
                    result=await service.fetch('public',{'sector_financials_v1':params},action,ttl=3600)
                    requests.append({'report':report,'page':page,'codes':codes,'ok':result.ok,'error':result.error,
                                     'url':result.metadata.get('url'),'cached':result.cached})
                    if not result.ok: break
                    for row in result.data.get('data',[]):
                        if row.get('SECURITY_CODE') not in codes: continue
                        end=str(row.get(period_key,''))[:10]; published=str(row.get('NOTICE_DATE',''))[:10]
                        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}',end) or not published or max(end,published)>cutoff: continue
                        code=symbol(row.get('SECUCODE') or row['SECURITY_CODE'])
                        output.setdefault(code,[]).append({'report':report,'period':end,'published_at':published,
                                                          'row':row,'url':result.metadata.get('url')})
                    if page>=result.data.get('pages',1): break
    return output,requests

def normalize_financials(records,asset,cutoff):
    grouped={}
    # Merge only vintages available by cutoff, oldest first so newer disclosure wins.
    for record in sorted(records,key=lambda r:r.get('published_at','')):
        if not record.get('published_at') or record['published_at']>cutoff or record['period']>cutoff: continue
        end=record['period']; record_values={MAP[k]:v for k,v in record['row'].items() if k in MAP and number(v) is not None}
        group=grouped.setdefault(end,{'values':{},'sources':[],'published_at':record['published_at']})
        group['values'].update(record_values)
        group['sources'].append({'report':record['report'],'published_at':record['published_at'],'url':record['url']})
        group['published_at']=max(group['published_at'],record['published_at'])
    payload=[]
    for end,group in grouped.items():
        label=end[:4]+{'03-31':'一季报','06-30':'中报','09-30':'三季报','12-31':'年报'}.get(end[5:],'未知')
        payload.append({'columns':[asset['name']+'('+asset['code']+')',label],
            'items':[[FIELDS[k][0],str(v)+('%' if k.endswith('_pct') else '元')] for k,v in group['values'].items()]})
    data=financial_metrics(payload,asset['code'],asset['name'],cutoff)
    for period in data['periods']:
        group=grouped[period['period']]
        period.update(published_at=group['published_at'],source_tables=group['sources'],publication_date_source='东方财富公开财报表',
                      publication_date_verified=False)
    data['limits'][0]='披露日来自东方财富公开财报结构化记录，已按截止日过滤；并非交易所逐项核验或历史版本数据库。'
    return data

def rank_candidates(assets,financials,cutoff,style):
    profile=PROFILES[style]; rows=[]
    for asset in assets:
        raw=asset['screen_row']; periods=financials.get(asset['code'],{}).get('periods',[])
        latest=periods[0] if periods else {}; values=latest.get('values',{}); derived=latest.get('derived',{})
        income_basis='total_operating_income' if derived.get('total_operating_income_yoy_pct') is not None else 'revenue'
        income_growth=derived.get(income_basis+'_yoy_pct')
        price,price_key=metric(raw,[r'最新价',r'收盘价']); amount,amount_key=metric(raw,['成交额'])
        pe,pe_key=metric(raw,[r'市盈率.*TTM',r'PE.*TTM']); momentum,momentum_key=metric(raw,[r'20.*涨跌幅',r'近一月.*涨跌幅',r'近1月.*涨跌幅',r'区间涨跌幅'])
        as_of=observation_date(price_key)
        reasons=[]
        for k in CORE:
            if k=='revenue' and values.get('total_operating_income') is not None: continue
            if values.get(k) is None: reasons.append('缺少'+FIELDS[k][0])
        if not latest: reasons.append('本次未取到可解析财报，不代表未披露')
        elif (date.fromisoformat(cutoff)-date.fromisoformat(latest['period'])).days>220: reasons.append('财报期间过旧')
        for label,v in [('现价',price),('成交额',amount),('PE(TTM)',pe)]:
            if v is None: reasons.append('缺少'+label)
        if not as_of or as_of>cutoff or (date.fromisoformat(cutoff)-date.fromisoformat(as_of)).days>7: reasons.append('行情日期未确认或陈旧')
        for label,key in [('成交额',amount_key),('PE(TTM)',pe_key)]+([('20日收益',momentum_key)] if profile['weights']['trend']>0 else []):
            if key and observation_date(key)!=as_of: reasons.append(label+'日期与行情不一致或未提供')
        if amount is not None and amount<3e7: reasons.append('成交额不足3000万元')
        if any(values.get(k) is not None and values[k]<=0 for k in ('profit_parent','profit_ex_items')): reasons.append('归母或扣非亏损，暂不进入正向推荐候选')
        if price is not None and price<=0: reasons.append('现价非正值')
        if pe is not None and pe<=0: reasons.append('PE(TTM)非正值')
        if momentum is not None and abs(momentum)>100: reasons.append('20日涨跌幅超过100%，需先核验上市及公司行动，暂不自动推荐')
        if profile['weights']['trend']>0 and momentum is None: reasons.append('缺少20日收益，无法评价本风格趋势')
        if style=='value' and (values.get('operating_cashflow') is None or values['operating_cashflow']<=0): reasons.append('价值风格要求本期经营现金流为正')
        if style in ('short','trend') and momentum is not None and momentum<=0: reasons.append('趋势类风格要求20日趋势为正')
        if style=='growth' and ((income_growth or 0)<=0 or (derived.get('profit_ex_items_yoy_pct') or 0)<=0): reasons.append('成长风格要求收入与扣非同比均为正')
        if income_growth is None or derived.get('profit_ex_items_yoy_pct') is None: reasons.append('缺少可比上年同期或同期基数非正')
        # These are documented heuristic features, not expected returns or a backtest.
        cash=derived.get('cashflow_to_parent_profit')
        quality=(values.get('roe_pct',0) or 0)+max(-2,min(2,cash or 0))*5-(derived.get('debt_to_assets_pct',0) or 0)*.1
        growth=((income_growth or 0)+(derived.get('profit_ex_items_yoy_pct') or 0))/2
        rows.append({'code':asset['code'],'name':asset['name'],'segment':asset['segment'],'period':latest.get('period'),
            'published_at':latest.get('published_at'),'as_of':as_of,'price':price,'pe_ttm':pe,'amount_cny':amount,
            'revenue_yoy_pct':derived.get('revenue_yoy_pct'),'profit_ex_items_yoy_pct':derived.get('profit_ex_items_yoy_pct'),
            'income_growth_yoy_pct':income_growth,'income_growth_basis':FIELDS[income_basis][0],
            'roe_pct':values.get('roe_pct'),'operating_cashflow_cny':values.get('operating_cashflow'),'return20_pct':momentum,
            'eligible':not reasons,'reasons':reasons,'score':None,
            'features':{'quality':quality,'growth':growth,'value':-pe if pe else None,'trend':momentum,'liquidity':amount}})
    valid=[r for r in rows if r['eligible']]
    for row in valid:
        # Valuation and fundamentals are ranked within their subindustry where possible.
        components={}
        for key,weight in profile['weights'].items():
            if weight==0: components[key]=0.; continue
            peers=[r for r in valid if r['segment']==row['segment']] if key in ('quality','growth','value') else valid
            if len(peers)<3 and key in ('quality','growth','value'):
                components[key]=50.; continue  # Never silently compare engine PE with shipbuilding PE.
            values=[r['features'][key] for r in peers]
            v=row['features'][key]
            components[key]=100*(sum(x<v for x in values)+.5*sum(x==v for x in values))/len(values)
        row['components']=components
        row['score']=round(sum(components[k]*w for k,w in profile['weights'].items()),2)
    return sorted(rows,key=lambda r:(not r['eligible'],-(r['score'] or 0),r['code']))

async def screen_sector(settings,service,topic,cutoff,progress=None):
    if date.fromisoformat(cutoff)!=date.fromisoformat(now()[:10]):
        raise DataError('SCREEN_ASOF','行业筛选使用当前成分与行情；历史日期请指定股票研究，不能把当前股票池当作历史成分。')
    style=settings.investment_style
    if style not in PROFILES: raise DataError('STYLE','未知投资风格。')
    limit=settings.research_pool_limit
    scope=topic
    query=f'当前A股{scope}全部成分股，最多{limit}只，按股票代码排序，剔除ST和退市。返回代码、名称、申万行业分类、最新价及日期、成交额、PE(TTM)、近20个交易日涨跌幅。不预先按估值或涨幅选出少量股票。'
    from .public_screen import public_sector_pool
    attempts=[]
    if settings.enable_miaoxiang:
        if progress: progress(f'妙想 Skill 获取行业候选池与行情（最多{limit}只）')
        result=await service.skill('mx-stocks-screener',query,limit=limit)
        attempts.append({'source':'miaoxiang_skill','ok':result.ok,'error':result.error})
    else: result=Result(source='miaoxiang',ok=False)
    if settings.enable_miaoxiang and (not result.ok or not candidates(result.data,topic,limit)[0]):
        result=await service.fetch('miaoxiang',{'sector_screen_v1':query},lambda:service.mx.mcp('mx_stocks_screener',query),900)
        attempts.append({'source':'miaoxiang_mcp','ok':result.ok,'error':result.error})
    if not result.ok or not candidates(result.data,topic,limit)[0]:
        if progress: progress('使用开源公开接口路线获取行业股票池，无需妙想额度')
        result=await public_sector_pool(service,topic,cutoff,limit,progress)
        attempts.append({'source':'public_sector_screen','ok':result.ok,'error':result.error})
    assets,rejected=candidates(result.data,topic,limit)
    if not assets: raise DataError('SECTOR_POOL_EMPTY','未取得可核对代码、名称和行业的股票池。')
    if progress: progress(f'已取得{len(assets)}只候选；分批读取公开利润表、资产负债表、现金流量表')
    records,requests=await public_financials(service,assets,cutoff)
    financials={a['code']:normalize_financials(records.get(a['code'],[]),a,cutoff) for a in assets}
    # MCP enriches known securities in bounded batches; narrow indicators avoid oversized answers.
    for offset in (range(0,len(assets),40) if settings.enable_miaoxiang and result.source!='public_sector_screen' else []):
        batch=assets[offset:offset+40]
        query='查询'+ '、'.join(a['name']+a['code'][:6] for a in batch)+f'在{cutoff}的PE(TTM)和近20个交易日涨跌幅，仅这两个指标，返回每只股票，注明指标日期。'
        response=await service.fetch('miaoxiang',{'sector_metrics_v1':query},lambda q=query:service.mx.mcp('mx_ashare_finance_data',q),900)
        attempts.append({'source':'miaoxiang_mcp_metrics','ok':response.ok,'error':response.error,'count':len(batch)})
        if response.ok:
            for table in tables(response.data):
                columns=table['columns']
                for asset in batch:
                    positions=[i for i,c in enumerate(columns) if asset['code'][:6] in str(c) or asset['name'] in str(c)]
                    # Native response is either issuer-first time columns or time-first issuer columns.
                    if positions==[0] and len(columns)>1:
                        date_columns=[i for i,c in enumerate(columns[1:],1) if str(c).replace('.','-')[:10]==cutoff]
                        index=date_columns[-1] if date_columns else None
                    elif positions and str(columns[0]).replace('.','-')[:10]==cutoff: index=positions[0]
                    else: index=None
                    if index is None: continue
                    for row in table['items']:
                        if len(row)>index and re.search('TTM|20.*涨跌幅|区间涨跌幅',str(row[0]),re.I):
                            label=str(row[0])
                            if '涨跌幅' in label: label='近20日涨跌幅'
                            asset['screen_row'][label+' '+cutoff]=row[index]
    ranked=rank_candidates(assets,financials,cutoff,style)
    selected=[r['code'] for r in ranked if r['eligible']][:settings.research_top_n]
    audit={'topic':topic,'cutoff':cutoff,'profile':{'id':style,**PROFILES[style]},'candidate_count':len(assets),
        'supplier_total':result.data.get('total_rows') if isinstance(result.data,dict) else None,
        'pool_coverage':result.data.get('coverage') if isinstance(result.data,dict) else None,
        'pool_limit':limit,'eligible_count':sum(r['eligible'] for r in ranked),'selected_codes':selected,
        'rows':ranked,'scope_rejections':rejected,'source_attempts':attempts,'financial_requests':requests,
        'financial_coverage':sum(bool(f['periods']) for f in financials.values()),
        'raw_screen_rows':[a['screen_row'] for a in assets],
        'pool_source':result.source,'pool_provenance':{**result.metadata,
            'requests':result.data.get('public_provenance',[]) if isinstance(result.data,dict) else []},
        'limitations':['候选池来自本次标注的数据源行业分类，主题行业映射仅作发现线索，具体公司业务关联须由公告核验。',
            '评分是透明的风格启发式分位数，不是收益概率，也未经策略回测。',
            '质量/成长/估值在相同子行业内比较；不足3个合格同业时该项记中性50分。',
            '公开网页数据服务可能变化；未取得、无可比基数和已披露是不同状态。'],
        'formula':'质量=ROE+clip(经营现金流/归母利润,-2,2)*5-负债率*0.1；成长=(收入同比+扣非同比)/2，收入优先用可比营业总收入，否则营业收入，逐项保留income_growth_basis，不混算同一公司同期口径；价值=-PE(TTM)；趋势=20日收益；流动性=成交额。先分位，再按所选权重加总。'}
    return [next(a for a in assets if a['code']==code) for code in selected],financials,audit
