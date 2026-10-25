"""Public evidence provenance and bounded retrieval from named publishers."""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import re
from urllib.parse import urlsplit, urlunsplit, urljoin

import httpx
import pandas as pd
from bs4 import BeautifulSoup

from .common import DataError, now, TZ
from .ashare_metrics import tables


SOURCE_GROUPS = {
    'official_disclosure': ('sse.com.cn', 'szse.cn', 'cninfo.com.cn', 'bse.cn'),
    'official_policy': ('gov.cn', 'mof.gov.cn', 'mod.gov.cn', 'miit.gov.cn', 'sasac.gov.cn', 'csrc.gov.cn', 'pbc.gov.cn'),
    'authoritative_media': ('news.cn', 'xinhuanet.com', 'people.com.cn', 'cnstock.com', 'stcn.com', 'cs.com.cn'),
    'vendor_reprint': ('pdf.dfcfw.com', 'eastmoney.com'),
}
SOURCE_DIRECTORY = [
    {'name':'巨潮资讯', 'url':'https://www.cninfo.com.cn/', 'purpose':'定期报告、临时公告；披露平台原文'},
    {'name':'上交所', 'url':'https://www.sse.com.cn/', 'purpose':'沪市公告、问询、交易规则'},
    {'name':'深交所', 'url':'https://www.szse.cn/', 'purpose':'深市公告、问询、交易规则'},
    {'name':'北交所', 'url':'https://www.bse.cn/', 'purpose':'北交所公告与规则'},
    {'name':'证监会', 'url':'https://www.csrc.gov.cn/', 'purpose':'监管政策'},
    {'name':'国务院', 'url':'https://www.gov.cn/', 'purpose':'宏观政策原文'},
    {'name':'财政部', 'url':'https://www.mof.gov.cn/', 'purpose':'预算与财政政策'},
    {'name':'工信部', 'url':'https://www.miit.gov.cn/', 'purpose':'产业政策'},
    {'name':'国资委', 'url':'https://www.sasac.gov.cn/', 'purpose':'央企改革及公开消息'},
    {'name':'新华社', 'url':'https://www.news.cn/', 'purpose':'权威新闻，区别于公司公告'},
    {'name':'证券时报', 'url':'https://www.stcn.com/', 'purpose':'财经报道与机构观点'},
    {'name':'上海证券报', 'url':'https://www.cnstock.com/', 'purpose':'财经报道与机构观点'},
    {'name':'中国证券报', 'url':'https://www.cs.com.cn/', 'purpose':'财经报道与机构观点'},
]
def source_tier(url: str) -> str:
    try:
        parts = urlsplit(url)
        host = (parts.hostname or '').lower()
        if parts.scheme not in ('http','https') or parts.username or parts.password or parts.port not in (None,80,443):
            return 'unverified'
        for tier, domains in SOURCE_GROUPS.items():
            if any(host == domain or host.endswith('.'+domain) for domain in domains):
                return tier
    except ValueError:
        pass
    return 'unverified'


def canonical_url(value: str) -> str:
    parts = urlsplit(value.strip())
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, parts.query, ''))


def news_records(payload, cutoff: str, keywords: list[str], start: str = '') -> dict:
    accepted, rejected, seen = [], [], set()
    cutoff_time = min(pd.Timestamp(cutoff + ' 23:59:59', tz='Asia/Shanghai'), pd.Timestamp(now()))
    for table in tables(payload):
        cols = table['columns']
        if '标题' not in cols:
            continue
        for row in table['items']:
            if not isinstance(row, list):
                continue
            raw = dict(zip(cols, row))
            title = str(raw.get('标题') or '')
            url = str(raw.get('跳转链接') or raw.get('链接') or '')
            timestamp = str(raw.get('发布时间') or raw.get('公告日期') or '')
            text = str(raw.get('摘要') or '')
            record = {'title':title, 'url':url, 'published_at':timestamp,
                      'publisher':str(raw.get('来源') or '未标注'), 'summary':text[:2000],
                      'origin':'miaoxiang_search', 'source_tier':source_tier(url)}
            reason = None
            try:
                stamp = pd.Timestamp(timestamp)
                if pd.isna(stamp):
                    raise ValueError('missing time')
                stamp = stamp.tz_localize('Asia/Shanghai') if stamp.tzinfo is None else stamp.tz_convert('Asia/Shanghai')
                if stamp > cutoff_time: reason = 'future_publication'
                elif start and stamp.date().isoformat() < start: reason = 'outside_window'
            except (ValueError, TypeError):
                reason = 'unknown_publication_date'
            if not any(word in title + text for word in keywords): reason = 'not_relevant'
            if record['source_tier'] == 'unverified': reason = 'unverified_domain'
            if reason:
                rejected.append({'title':title, 'reason':reason})
                continue
            record['url'] = canonical_url(url)
            key = record['url']
            if key not in seen:
                seen.add(key)
                accepted.append(record)
    return {'accepted':accepted[:12], 'rejected':rejected,
            'limits':'供应商检索摘要是检索证据；只有正文抓取成功才标正文已取回。域名可信不代表具体内容已核验。'}


def cached_disclosure(record: dict, directory: Path) -> dict | None:
    """Reuse immutable official PDF URLs only, validating recorded binary hashes."""
    if source_tier(record['url'])!='official_disclosure' or not record['url'].lower().endswith('.pdf'):
        return None
    runs=directory.parent.parent.resolve()
    if runs.name!='runs': return None
    for packet in sorted(runs.glob('*/packet.json'),key=lambda p:p.stat().st_mtime,reverse=True)[:30]:
        try:
            values=json.loads(packet.read_text(encoding='utf-8'))['evidence']
            for item in values:
                value=item.get('data',{})
                if item.get('kind')!='document' or value.get('url')!=record['url'] or value.get('status')!='body_retrieved': continue
                original=Path(value['local_file']).resolve()
                if not original.is_relative_to(runs) or not original.is_file(): continue
                raw=original.read_bytes()
                if hashlib.sha256(raw).hexdigest()!=value.get('sha256'): continue
                text_path=original.with_suffix('.txt')
                body=text_path.read_text(encoding='utf-8')
                if len(body)!=value.get('chars') or len(body)<100: continue
                text_hash=hashlib.sha256(body.encode()).hexdigest()
                if value.get('text_sha256') and value['text_sha256']!=text_hash: continue
                directory.mkdir(parents=True,exist_ok=True)
                target=directory/original.name
                target.write_bytes(raw)
                target.with_suffix('.txt').write_text(body,encoding='utf-8')
                return {**value,**record,'local_file':str(target),'cached':True,'cache_checked_at':now(),
                        'text_sha256':text_hash,'cache_origin':str(original)}
        except (OSError,ValueError,KeyError,TypeError):
            continue
    return None


async def fetch_document(record: dict, directory: Path) -> dict:
    """Never follow an untrusted redirect or save credentials/cookies."""
    result = {**record, 'source_tier':source_tier(record['url']), 'fetched_at':now(), 'status':'unavailable'}
    cached=cached_disclosure(record,directory)
    if cached: return cached
    try:
        url = record['url']
        async with httpx.AsyncClient(timeout=httpx.Timeout(25, connect=10), follow_redirects=False,
                                     headers={'User-Agent':'Mozilla/5.0 AShareResearch/1.0'}) as client:
            for _ in range(4):
                if source_tier(url) == 'unverified':
                    raise DataError('SOURCE_DOMAIN', '未登记域名；不自动访问。')
                async with client.stream('GET', url) as response:
                    if response.is_redirect:
                        url = urljoin(url, response.headers.get('location',''))
                        continue
                    response.raise_for_status()
                    content = bytearray()
                    async for chunk in response.aiter_bytes():
                        content.extend(chunk)
                        if len(content) > 15_000_000:
                            raise DataError('DOCUMENT_SIZE', '文档超过15MB上限。')
                    encoding = response.encoding or 'utf-8'
                break
            else:
                raise DataError('REDIRECT_LIMIT', '重定向次数过多。')
        digest = hashlib.sha256(content).hexdigest()
        directory.mkdir(parents=True, exist_ok=True)
        if content[:4] == b'%PDF':
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(content))
            pages = [page.extract_text() or '' for page in reader.pages[:180]]
            text = '\n'.join(pages)
            path = directory / (digest[:20]+'.pdf')
            result['pages_read'] = len(pages)
            result['pages_total'] = len(reader.pages)
            # Preserve the financial section and a few relevant operating/risk paragraphs.
            selected = [text[:7000]]
            for page in pages:
                if any(x in page for x in ('主要会计数据','主营业务','经营情况讨论','应收账款','合同负债','重大风险')):
                    selected.append(page[:3500])
            excerpt = '\n'.join(selected)[:22000]
        else:
            html = bytes(content).decode(encoding, errors='replace')
            soup = BeautifulSoup(html, 'html.parser')
            date_node = soup.find('meta',attrs={'property':'article:published_time'}) or soup.find('meta',attrs={'name':'publishdate'})
            date_text = date_node.get('content','') if date_node else ''
            # stcn's public article header carries the publication timestamp.
            title_node = soup.find('h1') or soup.select_one('.detail-title')
            info_node = soup.select_one('.detail-info')
            if not date_text and info_node:
                match = re.search(r'20\d{2}[-/]\d{2}[-/]\d{2}\s+\d{2}:\d{2}',info_node.get_text(' ',strip=True))
                if match: date_text=match[0]
            article = soup.select_one('.article-content') or soup.select_one('.detail-content') or soup.find('article')
            for node in soup(['script','style','nav','footer','header']): node.decompose()
            whole_text = soup.get_text(' ', strip=True)
            if not date_text and title_node:
                after_title = whole_text.split(title_node.get_text(' ',strip=True),1)[-1]
                match = re.search(r'20\d{2}[-/]\d{2}[-/]\d{2}\s+\d{2}:\d{2}',after_title[:1200])
                if match: date_text=match[0]
            text = article.get_text(' ',strip=True) if article else whole_text
            path = directory / (digest[:20]+'.html')
            excerpt = text[:14000]
            if date_text:
                result['body_published_at']=date_text
                expected = str(record.get('published_at',''))[:10]
                if expected and date_text[:10].replace('/','-')!=expected:
                    result['date_conflict']={'search_date':expected,'body_date':date_text[:10]}
        if len(text.strip()) < 100 or any(x in text[:4000].lower() for x in ('access denied','verify you are human','访问过于频繁','安全验证')):
            raise DataError('BODY_NOT_AVAILABLE', '正文为空或被访问校验拦截。')
        path.write_bytes(content)
        path.with_suffix('.txt').write_text(text, encoding='utf-8')
        result.update(status='body_retrieved', final_url=url, source_tier=source_tier(url),
                      sha256=digest, local_file=str(path), excerpt=excerpt, chars=len(text),
                      text_sha256=hashlib.sha256(text.encode()).hexdigest(),content_verified=False)
        if result.get('date_conflict'):
            result.update(status='date_conflict',excerpt='原文日期与检索日期冲突，内容已隔离，不得用于本期事件判断。')
    except Exception as exc:
        result['error'] = {'code':getattr(exc,'code',type(exc).__name__),
                           'message':str(exc) if isinstance(exc,DataError) else '公开正文未取得；保留来源链接与检索状态。'}
    return result


async def official_disclosures(assets: list[dict], start: str, end: str, report_only=True) -> dict:
    """Bounded public CNInfo disclosure search, following AKShare's documented route.

    Retain the official adjunctUrl instead of the JavaScript detail landing page.
    One first page per issuer; never claim exhaustive announcement coverage.
    """
    accepted, errors = [], []
    requests = 0
    try:
        async with httpx.AsyncClient(timeout=25,follow_redirects=False,headers={
            'User-Agent':'Mozilla/5.0','Referer':'https://www.cninfo.com.cn/'}) as client:
            requests += 1
            response=await client.get('https://www.cninfo.com.cn/new/data/szse_stock.json')
            response.raise_for_status()
            mapping={str(x['code']):x['orgId'] for x in response.json().get('stockList',[])}
            for asset in assets:
                try:
                    code=asset['code'][:6]
                    if code not in mapping: raise DataError('IDENTITY_NOT_FOUND','官方证券映射未找到该代码。')
                    requests += 1
                    response=await client.post('https://www.cninfo.com.cn/new/hisAnnouncement/query',data={
                        'pageNum':'1','pageSize':'20','column':'szse','tabName':'fulltext','plate':'',
                        'stock':code+','+mapping[code],'searchkey':'','secid':'',
                        'category':'category_bndbg_szsh;category_sjdbg_szsh;category_ndbg_szsh;' if report_only else '',
                        'trade':'','seDate':start+'~'+end,'sortName':'time','sortType':'desc','isHLtitle':'false'})
                    response.raise_for_status()
                    for item in response.json().get('announcements') or []:
                        if str(item.get('secCode'))!=code: continue
                        official_name=str(item.get('secName') or '').replace(' ','')
                        if official_name and official_name!=asset['name'].replace(' ',''):
                            errors.append({'symbol':asset['code'],'code':'NAME_MISMATCH','official_name':official_name})
                            continue
                        stamp=pd.to_datetime(item['announcementTime'],unit='ms',utc=True).tz_convert('Asia/Shanghai')
                        if not start<=stamp.date().isoformat()<=end: continue
                        title=BeautifulSoup(str(item['announcementTitle']),'html.parser').get_text()
                        accepted.append({'title':(official_name or asset['name'])+':'+title,'published_at':stamp.isoformat(),
                            'publisher':official_name or asset['name'],'symbol':asset['code'],'origin':'cninfo_public_disclosure',
                            'source_tier':'official_disclosure','url':urljoin('https://static.cninfo.com.cn/',item['adjunctUrl'])})
                except Exception as exc:
                    errors.append({'symbol':asset['code'],'code':getattr(exc,'code',type(exc).__name__)})
    except Exception as exc:
        errors.append({'code':getattr(exc,'code',type(exc).__name__)})
    return {'accepted':accepted,'errors':errors,'requests':requests,
            'limits':('每家公司首20条定期报告检索' if report_only else '每家公司最近20条公告检索')+'，非全部公告；未将预约披露日冒充实际披露日。'}
