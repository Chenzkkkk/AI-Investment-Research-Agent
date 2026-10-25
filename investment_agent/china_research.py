"""Bounded official-source discovery, with explicit coverage and date limits."""
import asyncio
import re
from datetime import date,timedelta
from urllib.parse import urljoin,urlsplit
from bs4 import BeautifulSoup
from .common import Result,now
from .intelligence.collect import Fetcher,extract_document

OFFICIAL_INDEXES={
 'macro-policy': [('国家统计局','https://www.stats.gov.cn/sj/zxfb/'),('国务院','https://www.gov.cn/zhengce/zuixin/'),('发改委','https://www.ndrc.gov.cn/xxgk/jd/')],
 'business-industry': [('工信部','https://www.miit.gov.cn/'),('发改委','https://www.ndrc.gov.cn/xxgk/jd/')],
 'debt-exposure': [('央行','https://www.pbc.gov.cn/'),('财政部','https://www.mof.gov.cn/zhengwuxinxi/')],
 'governance': [('证监会','https://www.csrc.gov.cn/')],
 'frontier-commercialization': [('工信部','https://www.miit.gov.cn/'),('国务院','https://www.gov.cn/zhengce/zuixin/')],
 'counter-evidence': [('证监会','https://www.csrc.gov.cn/')],
}


def fetch(url):
    client=Fetcher()
    try: return client.get(url)
    finally: client.close()


async def discover_official(service,query,dimension,as_of=None,code=None,name=None):
    cutoff=(as_of or now())[:10]; evidence=[]; requests=[]
    if not service.settings.enable_public:
        return {'evidence':[],'requests':[],'limits':['公开来源关闭；未请求官方页面。']}
    if code and name:
        from .ashare_sources import official_disclosures
        start=(date.fromisoformat(cutoff)-timedelta(days=365)).isoformat()
        async def action(): return Result(source='cninfo_public',data=await official_disclosures([{'code':code,'name':name}],start,cutoff,report_only=False))
        result=await service.fetch('public',{'governance_index':code,'cutoff':cutoff},action,3600)
        requests.append({'source':'巨潮公告','ok':result.ok,'error':result.error})
        if result.ok: evidence.extend({**x,'coverage':'公告索引，尚未读全文'} for x in result.data.get('accepted',[]))
    keywords=re.findall(r'[A-Za-z]{2,}|人工智能|机器人|半导体|政策|财政|货币|信贷|利率|物价|消费|出口|融资|研发|质押|回购|审计|产业',query)
    for publisher,url in OFFICIAL_INDEXES.get(dimension,[])[:3]:
        async def action(url=url):
            body,headers,final,status=await asyncio.to_thread(fetch,url)
            soup=BeautifulSoup(body,'html.parser'); links=[]
            for a in soup.find_all('a',href=True):
                title=a.get_text(' ',strip=True); target=urljoin(final,a['href'])
                host=urlsplit(target).hostname or ''
                if len(title)<12 or not host.endswith('.gov.cn') or not target.startswith('https://'): continue
                if keywords and not any(k.lower() in title.lower() for k in keywords): continue
                if target not in [x['url'] for x in links]: links.append({'title':title,'url':target,'publisher':publisher,'published_at':None,'coverage':'标题线索，需读原文核查时间与事实'})
                if len(links)>=8: break
            return Result(source='official_index',data={'links':links},metadata={'url':final})
        result=await service.fetch('public',{'official_index':url,'query':query,'day':cutoff},action,1800)
        requests.append({'source':publisher,'url':url,'ok':result.ok,'error':result.error})
        if result.ok: evidence.extend(result.data.get('links',[]))
    # Read a few relevant primary texts. Unknown publication times stay unknown.
    for entry in evidence[:3]:
        async def action(entry=entry):
            body,headers,final,status=await asyncio.to_thread(fetch,entry['url'])
            document=await asyncio.to_thread(extract_document,body,final)
            return Result(source='official_document',data={'url':final,**document})
        result=await service.fetch('public',{'official_body':entry['url']},action,3600)
        if result.ok:
            doc=result.data
            if doc.get('published') and doc['published'][:10]>cutoff:
                entry.update(coverage='晚于研究截止日，排除',body=''); continue
            entry.update(body=doc.get('body','')[:12000],published_at=entry.get('published_at') or doc.get('published'),
                         coverage='原文截取' if doc.get('body') else '正文未解析')
        else: entry.update(error=result.error)
    return {'evidence':evidence,'requests':requests,'as_of':cutoff,
            'limits':['仅已列官方入口和本轮匹配链接，不是全网或所有主管部门覆盖。',
                      '未知发布日期材料不能作为历史时点已发生事件；政策、观点、执行结果分开判断。']}
