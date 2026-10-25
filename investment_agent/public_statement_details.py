"""Bounded public statement-detail fallback, matching AKShare's documented source.

Only supplies operating revenue omitted by the bulk feed. No total-revenue alias.
"""
import re
import httpx
from bs4 import BeautifulSoup
from .common import Result,DataError,number

BASE='https://emweb.securities.eastmoney.com/PC_HSF10/NewFinanceAnalysis/'


async def supplement_revenue(service,records,asset,cutoff):
    periods=sorted({r['period'] for r in records if r.get('published_at') and max(r['period'],r['published_at'])<=cutoff},reverse=True)
    existing={r['period'] for r in records if number(r['row'].get('OPERATE_INCOME')) is not None}
    missing=[p for p in periods if p not in existing][:24]
    if not missing: return [],[]
    code=asset['code'];webcode=code[-2:]+code[:6];requests=[];out=[]
    async with httpx.AsyncClient(timeout=25,trust_env=False) as client:
        async def category():
            response=await client.get(BASE+'Index',params={'type':'web','code':webcode.lower()});response.raise_for_status()
            tag=BeautifulSoup(response.text,'html.parser').find(id='hidctype')
            if not tag or not re.fullmatch(r'\d+',tag.get('value','')): raise DataError('STATEMENT_CATEGORY','公开财报公司类型未取得。')
            return Result(source='eastmoney_public',kind='statement_category',data={'company_type':tag['value']},metadata={'url':str(response.url)})
        cat=await service.fetch('public',{'statement_category':code},category,ttl=86400)
        requests.append({'report':'statement_category','ok':cat.ok,'error':cat.error})
        if not cat.ok: return out,requests
        for offset in range(0,len(missing),5):
            dates=missing[offset:offset+5]
            params={'companyType':cat.data['company_type'],'reportDateType':'0','reportType':'1','code':webcode,'dates':','.join(dates)}
            async def fetch(params=params):
                response=await client.get(BASE+'lrbAjaxNew',params=params);response.raise_for_status()
                rows=response.json().get('data')
                if not isinstance(rows,list) or not rows: raise DataError('STATEMENT_DETAIL_EMPTY','公开利润表明细未返回数据。')
                return Result(source='eastmoney_public',kind='income_detail',data={'rows':rows},metadata={'url':str(response.url),'units':'CNY'})
            result=await service.fetch('public',{'income_detail_v1':params},fetch,ttl=3600)
            requests.append({'report':'income_detail','dates':dates,'ok':result.ok,'error':result.error,'url':result.metadata.get('url')})
            if not result.ok: break
            for row in result.data['rows']:
                period=str(row.get('REPORT_DATE',''))[:10];published=str(row.get('NOTICE_DATE',''))[:10]
                if row.get('SECURITY_CODE')!=code[:6] or row.get('SECUCODE')!=code: continue
                if period not in dates or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',published) or max(period,published)>cutoff: continue
                if number(row.get('OPERATE_INCOME')) is None: continue
                out.append({'period':period,'published_at':published,'report':'eastmoney_income_detail',
                    'url':result.metadata['url'],'row':{k:row.get(k) for k in ('SECURITY_NAME_ABBR','OPERATE_INCOME','TOTAL_OPERATE_INCOME')}})
    return out,requests
