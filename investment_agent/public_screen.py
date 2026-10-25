"""Credential-free sector screening using public routes documented by AKShare.

Data remains publisher-owned. No paid API, scraping login, or private endpoint.
"""
from datetime import date,timedelta
import re
import httpx
from .common import DataError,Result,symbol,number

API='https://datacenter-web.eastmoney.com/api/data/v1/get'
TECH_INDUSTRIES=['软件开发','计算机设备','半导体','通信设备','自动化设备','通用设备','电机']

def sector_condition(topic):
    """Explicit discovery proxies, not a claim that every issuer sells AI products."""
    if any(x in topic.lower() for x in ('ai','人工智能','机器人','前沿科技')):
        industries=TECH_INDUSTRIES
        if '机器人' in topic and not any(x in topic.lower() for x in ('ai','人工智能','前沿科技')):
            industries=['自动化设备','通用设备','电机']
        return '(PUBLISHNAME in ('+','.join('"'+x+'"' for x in industries)+'))'
    clean=re.sub(r'A股|行业|板块|研究|分析|机会|投资|建议','',topic).strip()
    if not re.fullmatch(r'[\u4e00-\u9fffA-Za-z]{2,15}',clean):
        raise DataError('PUBLIC_SECTOR_SCOPE','请使用明确主题，如AI、机器人、半导体或软件开发。')
    return f'(PUBLISHNAME like "%{clean}%")'

async def get_table(service,client,params):
    async def action():
        r=await client.get(API,params=params); r.raise_for_status(); data=r.json()
        if not data.get('success') or not data.get('result'):
            raise DataError('PUBLIC_SCREEN_EMPTY','公开数据中心未返回匹配行业或估值记录。')
        return Result(source='eastmoney_public',data=data['result'],metadata={'url':str(r.url)})
    return await service.fetch('public',{'public_screen_v1':params},action,900)

def parse_quotes(text,requested):
    """Use the feed's date and explicit price/volume/amount summary, not fetch time."""
    output={}
    for ticker,body in re.findall(r'v_([a-z]{2}\d{6})="([^"]+)"',text):
        code=symbol(ticker[2:]+'.'+ticker[:2].upper())
        fields=body.split('~')
        if code not in requested or len(fields)<36 or fields[2]!=code[:6]: continue
        stamp=fields[30]
        if not re.fullmatch(r'\d{14}',stamp): continue
        day=stamp[:4]+'-'+stamp[4:6]+'-'+stamp[6:8]
        triple=fields[35].split('/')
        if len(triple)!=3 or number(triple[2]) is None: continue
        output[code]={'name':fields[1],'as_of':day,'price':number(fields[3]),'amount':number(triple[2]),'timestamp':stamp}
    return output

async def public_sector_pool(service,topic,cutoff,limit=150,progress=None):
    condition=sector_condition(topic)
    condition+=f"(REPORTDATE>='{cutoff[:4]}-01-01')(NOTICE_DATE<='{cutoff}')"
    found={}; provenance=[]; errors=[]
    async with httpx.AsyncClient(timeout=25,trust_env=False) as client:
        for page in range(1,5):
            result=await get_table(service,client,{'reportName':'RPT_LICO_FN_CPD','columns':'ALL','filter':condition,
                'pageSize':500,'pageNumber':page,'sortColumns':'REPORTDATE,SECURITY_CODE','sortTypes':'-1,1'})
            if not result.ok: raise DataError('PUBLIC_POOL_FAILED',(result.error or {}).get('message','行业名单未取得'))
            provenance.append(result.metadata)
            for row in result.data.get('data',[]):
                code=symbol(row.get('SECUCODE') or row['SECURITY_CODE']); name=row['SECURITY_NAME_ABBR']
                if code in found or re.search('ST|退',name,re.I) or row.get('SECURITY_TYPE')!='A股': continue
                if str(row.get('NOTICE_DATE',''))[:10]>cutoff: continue
                found[code]={'代码':code,'名称':name,'所属行业':str(row['PUBLISHNAME'])}
            if page>=result.data.get('pages',1): break
        # Deterministic round robin prevents one high-volume industry filling the pool.
        groups={}
        for code in sorted(found): groups.setdefault(found[code]['所属行业'],[]).append(code)
        codes=[]
        while any(groups.values()) and len(codes)<limit:
            for group in sorted(groups):
                if groups[group] and len(codes)<limit: codes.append(groups[group].pop(0))
        if not codes: raise DataError('PUBLIC_POOL_EMPTY','公开财报行业分类没有匹配股票；未用固定样本替代。')
        if progress: progress(f'公开行业分类取得{len(codes)}只；批量核对历史估值与当日行情')
        start=(date.fromisoformat(cutoff)-timedelta(days=65)).isoformat()
        for offset in range(0,len(codes),10):
            batch=codes[offset:offset+10]; values={}
            filter_codes=','.join('"'+code[:6]+'"' for code in batch)
            for page in range(1,3):
                result=await get_table(service,client,{'reportName':'RPT_VALUEANALYSIS_DET','columns':'ALL',
                    'filter':f"(SECURITY_CODE in ({filter_codes}))(TRADE_DATE>='{start}')(TRADE_DATE<='{cutoff}')",
                    'sortColumns':'TRADE_DATE,SECURITY_CODE','sortTypes':'-1,1','pageSize':500,'pageNumber':page})
                if not result.ok: errors.append(result.error); break
                provenance.append(result.metadata)
                for row in result.data.get('data',[]):
                    code=symbol(row.get('SECUCODE') or row['SECURITY_CODE']); stamp=str(row['TRADE_DATE'])[:10]
                    if code in batch and start<=stamp<=cutoff: values.setdefault(code,{})[stamp]=row
                if page>=result.data.get('pages',1): break
            for code,observations in values.items():
                ordered=sorted(observations.items(),reverse=True); stamp,last=ordered[0]
                found[code]['最新价(元) '+stamp]=last.get('CLOSE_PRICE')
                found[code]['PE(TTM) '+stamp]=last.get('PE_TTM')
                found[code]['市净率(MRQ) '+stamp]=last.get('PB_MRQ')
                if len(ordered)>=21:
                    before=number(ordered[20][1].get('CLOSE_PRICE')); current=number(last.get('CLOSE_PRICE'))
                    if before and current: found[code]['近20日涨跌幅(%) '+stamp]=(current/before-1)*100
        for offset in range(0,len(codes),40):
            batch=codes[offset:offset+40]
            tickers=','.join(c[-2:].lower()+c[:6] for c in batch)
            async def action(tickers=tickers,batch=batch):
                response=await client.get('https://qt.gtimg.cn/q='+tickers)
                response.raise_for_status()
                quotes=parse_quotes(response.text,set(batch))
                if not quotes: raise DataError('PUBLIC_QUOTE_EMPTY','腾讯公开行情没有返回可核验日期和成交额。')
                return Result(source='tencent_public_quotes',data=quotes,metadata={'url':str(response.url)})
            result=await service.fetch('public',{'public_batch_quote':tickers,'date':cutoff},action,60)
            if not result.ok: errors.append(result.error); continue
            provenance.append(result.metadata)
            for code,quote in result.data.items():
                if code not in found or quote['as_of']>cutoff: continue
                if quote['name'].replace(' ','')!=found[code]['名称'].replace(' ',''): continue
                found[code]['成交额(元) '+quote['as_of']]=quote['amount']
    return Result(source='public_sector_screen',kind='screen',data={'rows':[found[c] for c in codes],
        'total_rows':len(found),'description':'公开财报行业分类；主题映射为发现线索，具体业务关联须另核验。池内按行业轮询、行业内代码排序截取。','public_provenance':provenance,'errors':errors},
        metadata={'mode':'credential_free','classification':'东方财富公开财报行业分类','pool_limit':limit})
