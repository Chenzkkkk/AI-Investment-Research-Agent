"""Bounded public HTTP collection with DNS pinning and redirect validation."""
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import unescape
import ipaddress
import json
import re
import socket
from urllib.parse import urljoin, urlsplit, urlunsplit, parse_qsl, urlencode

import feedparser
import httpx
from bs4 import BeautifulSoup
from .store import TZ, now, dumps


class FetchError(Exception):
    pass


def canonical(url):
    p=urlsplit(url)
    if p.scheme not in ('http','https') or not p.hostname or p.username or p.password:
        raise FetchError('仅允许无凭据的公开 HTTP(S) 地址')
    if p.port not in (None,80,443): raise FetchError('不允许非标准端口')
    query=urlencode([(k,v) for k,v in parse_qsl(p.query,keep_blank_values=True)
                     if not k.lower().startswith('utm_') and k.lower() not in ('fbclid','gclid')])
    if any(k.lower() in ('token','access_token','api_key','apikey','key','auth','authorization') for k,v in parse_qsl(query)):
        raise FetchError('不接收带凭据参数的链接')
    return urlunsplit((p.scheme,p.netloc.lower(),p.path or '/',query,''))


def public_addresses(host,port):
    if host.lower()=='localhost' or host.lower().endswith(('.local','.localhost','.internal')):
        raise FetchError('禁止本机或内网地址')
    addresses=list(dict.fromkeys(r[4][0] for r in socket.getaddrinfo(host,port,type=socket.SOCK_STREAM)))
    if not addresses or any(not ipaddress.ip_address(a).is_global for a in addresses):
        raise FetchError('DNS 指向非公开网络，已拒绝')
    return addresses


class Fetcher:
    def __init__(self, max_bytes=8_000_000):
        self.max_bytes=max_bytes
        self.client=httpx.Client(timeout=httpx.Timeout(20,connect=8),trust_env=False,follow_redirects=False)

    def close(self): self.client.close()

    def get(self,url,headers=None):
        url=canonical(url)
        for _ in range(5):
            p=urlsplit(url); port=p.port or (443 if p.scheme=='https' else 80)
            addresses=public_addresses(p.hostname,port)
            # Connect to the validated address, retain original TLS SNI + certificate verification.
            # This closes the DNS-rebinding window between validation and connect.
            address=sorted(addresses,key=lambda a:':' in a)[0]
            iphost='['+address+']' if ':' in address else address
            pinned=urlunsplit((p.scheme,iphost+(':'+str(p.port) if p.port else ''),p.path,p.query,''))
            with self.client.stream('GET',pinned,headers={
                'Host':p.netloc,'User-Agent':'ZhiHengResearch/1.0 (personal public-feed reader)',
                'Accept':'application/rss+xml, application/atom+xml, application/json, text/html, */*',**(headers or {})},
                extensions={'sni_hostname':p.hostname}) as response:
                if response.status_code in (301,302,303,307,308):
                    url=canonical(urljoin(url,response.headers.get('location',''))); continue
                if response.status_code==304: return b'',dict(response.headers),url,304
                if response.status_code!=200: raise FetchError('HTTP '+str(response.status_code))
                parts=[]; size=0
                for part in response.iter_bytes():
                    size+=len(part)
                    if size>self.max_bytes: raise FetchError('响应超过8MB限制')
                    parts.append(part)
                return b''.join(parts),dict(response.headers),url,200
        raise FetchError('重定向超过限制')


def date(value):
    if not value: return None
    try:
        d=datetime.fromisoformat(str(value).replace('Z','+00:00'))
    except ValueError:
        try: d=parsedate_to_datetime(str(value))
        except (ValueError,TypeError,OverflowError): return None
    if d.tzinfo is None: d=d.replace(tzinfo=TZ)
    if d>datetime.now(timezone.utc)+timedelta(minutes=5): return None
    return d.astimezone(TZ).isoformat(timespec='seconds')


def plain(value):
    return BeautifulSoup(value or '', 'html.parser').get_text(' ',strip=True)


def extract_document(data,url):
    if data.startswith(b'%PDF'):
        from io import BytesIO
        from pypdf import PdfReader
        pdf=PdfReader(BytesIO(data))
        return dict(body='\n'.join((p.extract_text() or '') for p in pdf.pages[:60])[:60000],published=None,author='',title='')
    import trafilatura
    doc=trafilatura.bare_extraction(data,url=url,include_comments=False,include_tables=True,with_metadata=True)
    if not doc: return dict(body='',published=None,author='',title='')
    # trafilatura >=2 returns a Document rather than a dict.
    if not isinstance(doc,dict): doc=doc.as_dict()
    return dict(body=(doc.get('text') or '')[:60000],published=date(doc.get('date')),
                author=doc.get('author') or '',title=doc.get('title') or '')


def discover(fetcher, source, previous):
    headers={}
    if previous.get('etag'): headers['If-None-Match']=previous['etag']
    if previous.get('modified'): headers['If-Modified-Since']=previous['modified']
    data,meta,final,status=fetcher.get(source['url'],headers)
    if status==304: return [],meta,True
    entries=[]
    if source['kind']=='rss':
        feed=feedparser.parse(data)
        if not feed.version: raise FetchError('返回内容不是有效 RSS/Atom 订阅')
        for item in feed.entries[:150]:
            if not item.get('link'): continue
            entries.append(dict(url=urljoin(final,item.link),title=plain(item.get('title','未命名')),
                published=date(item.get('published') or item.get('updated')),excerpt=plain(item.get('summary',''))[:6000],
                author=item.get('author','')))
    elif source['kind']=='html':
        soup=BeautifulSoup(data,'html.parser'); seen=set()
        for a in soup.select('a[href]'):
            url=urljoin(final,a['href']); title=a.get_text(' ',strip=True)
            if not re.search(source['pattern'],url) or len(title)<8 or url in seen: continue
            seen.add(url)
            entries.append(dict(url=url,title=title,published=None,excerpt='',author=''))
        if not entries: raise FetchError('目录页面未识别到文章，需更新解析规则')
    elif source['kind']=='hn':
        for identifier in json.loads(data)[:15]:
            item=json.loads(fetcher.get(f'https://hacker-news.firebaseio.com/v0/item/{int(identifier)}.json')[0])
            if not item or item.get('deleted') or item.get('dead'): continue
            hn=f'https://news.ycombinator.com/item?id={int(identifier)}'
            entries.append(dict(url=hn,title=plain(item.get('title','')),published=datetime.fromtimestamp(item['time'],TZ).isoformat(timespec='seconds'),
                excerpt=plain(item.get('text',''))[:4000]+'\n社区外链（尚未核验）：'+str(item.get('url') or ''),author=item.get('by',''),
                external_url=item.get('url')))
    return entries,meta,False


def collect(store,force=False,source_ids=None):
    settings=store.settings(); maximum=settings['max_documents']; fetched=0; inserted=0; results=[]
    fetcher=Fetcher(); cutoff=(datetime.now(TZ)-timedelta(days=7)).isoformat(timespec='seconds')
    try:
        for row in store.sources():
            s=row['config']
            if not s['enabled'] or s['kind']=='candidate' or (source_ids and s['id'] not in source_ids): continue
            if not force and row['next_due'] and row['next_due']>now(): continue
            interval=settings['research_minutes'] if s['interval']==1440 else settings['page_minutes'] if s['interval']==120 else settings['feed_minutes']
            due=(datetime.now(TZ)+timedelta(minutes=interval)).isoformat(timespec='seconds')
            stamp=now()
            with store.db() as db:
                populated=db.execute('SELECT 1 FROM articles WHERE source_id=? LIMIT 1',(s['id'],)).fetchone()
            source_cutoff=cutoff if populated else (datetime.now(TZ)-timedelta(days=30)).isoformat(timespec='seconds')
            try:
                discovered,meta,unchanged=discover(fetcher,s,row if populated else {})
                # Persist discovery before applying per-run limits. A subsequent 304 must
                # not strand articles that were discovered but not downloaded yet.
                with store.db() as db:
                    for item in discovered:
                        try:item['url']=canonical(item['url'])
                        except (FetchError,ValueError):continue
                        if item['published'] and item['published']<source_cutoff:continue
                        db.execute('INSERT OR IGNORE INTO crawl_queue VALUES(?,?,?,?,0)',(s['id'],item['url'],dumps(item),stamp))
                    queued=[json.loads(r[0]) for r in db.execute('SELECT payload FROM crawl_queue WHERE source_id=? AND done=0 ORDER BY queued,url LIMIT 150',(s['id'],))]
                entries=queued+[x for x in discovered if x['url'] not in {q['url'] for q in queued}]
                new_count=0; summary_count=0; errors=0
                # Per-source fairness prevents an initial large feed consuming the entire run.
                for entry in entries[:150]:
                    if entry['published'] and entry['published']<source_cutoff:
                        with store.db() as db:db.execute('UPDATE crawl_queue SET done=1 WHERE source_id=? AND url=?',(s['id'],entry['url']))
                        continue
                    if new_count>=6 or fetched>=maximum: break
                    try: entry['url']=canonical(entry['url'])
                    except (FetchError,ValueError): continue
                    with store.db() as db:
                        old=db.execute('SELECT * FROM articles WHERE url=?',(entry['url'],)).fetchone()
                    # Recheck known documents once daily to find corrections, independent of feed metadata.
                    if old and old['updated'][:10]==stamp[:10]:
                        with store.db() as db:db.execute('UPDATE crawl_queue SET done=1 WHERE source_id=? AND url=?',(s['id'],entry['url']))
                        continue
                    body=''; coverage='仅摘要'
                    try:
                        fetched+=1
                        raw,_,resolved,_=fetcher.get(entry['url'])
                        doc=extract_document(raw,resolved)
                        body=doc['body']
                        if len(body)>=120:
                            coverage='正文已提取'
                            entry['published']=entry['published'] or doc['published']
                            entry['author']=entry['author'] or doc['author']
                        else: body=''
                    except Exception as exc:
                        errors+=1
                        # Keep a known full document during a transient fetch failure.
                        if old: continue
                    if s['kind']=='hn' and entry.get('external_url') and fetched<maximum:
                        # Preserve the community item and independently retrieve the linked publisher.
                        try:
                            external=canonical(entry['external_url']); fetched+=1
                            raw,_,resolved,_=fetcher.get(external)
                            linked=extract_document(raw,resolved)
                            if len(linked['body'])>=120:
                                _,new=store.ingest(dict(url=external,title=linked['title'] or entry['title'],published=linked['published'],
                                    body=linked['body'],excerpt='发现入口：'+entry['url'],author=linked['author'],coverage='正文已提取',
                                    source_id=s['id'],domain=s['domain'],region=s['region'],tier='项目自述（社区发现）'))
                                inserted+=int(new)
                        except Exception: pass
                    if not body: summary_count+=1
                    # HN is a community statement; linked publisher identity is not HN identity.
                    article={**entry,'body':body,'coverage':coverage,'source_id':s['id'],
                             'domain':s['domain'],'region':s['region'],'tier':s['tier']}
                    _,changed=store.ingest(article)
                    with store.db() as db:db.execute('UPDATE crawl_queue SET done=1 WHERE source_id=? AND url=?',(s['id'],entry['url']))
                    inserted+=int(changed); new_count+=1
                status='已接通·无新条目' if unchanged or not entries else '已接通'
                if errors: status='已接通·部分正文失败'
                store.source_state(s['id'],status=status,checked=stamp,success=stamp,error=(f'{errors}篇正文读取失败；仅保留可用摘要' if errors else ''),
                    etag=meta.get('etag',row['etag']),modified=meta.get('last-modified',row['modified']),entries=len(discovered) if not unchanged else row['entries'],next_due=due)
                results.append(dict(source=s['name'],status=status,entries=len(discovered),processed=new_count,summary_only=summary_count))
            except Exception as exc:
                message=str(exc) if isinstance(exc,FetchError) else type(exc).__name__
                backoff=(datetime.now(TZ)+timedelta(minutes=max(interval,120))).isoformat(timespec='seconds')
                store.source_state(s['id'],status='暂时失败',checked=stamp,error=message[:200],next_due=backoff)
                results.append(dict(source=s['name'],status='暂时失败',error=message[:200]))
    finally: fetcher.close()
    return dict(downloads=fetched,new_or_updated=inserted,sources=results,
                coverage='增量最多补查近7天；无历史记录的来源初次回填近30天并按原始时间展示。列表截断、下线文章和关机期间内容可能无法补齐。')
