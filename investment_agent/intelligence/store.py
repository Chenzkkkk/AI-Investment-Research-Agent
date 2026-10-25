"""Short SQLite WAL transactions; immutable article/event revisions and daily budgets."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import uuid
from .sources import SOURCES, DEFAULTS, DOMAINS

TZ = timezone(timedelta(hours=8))


def now():
    return datetime.now(TZ).isoformat(timespec='seconds')


def dumps(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


def digest(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


class Store:
    def __init__(self, root):
        self.directory = Path(root)/'data/intelligence'
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory/'intelligence.sqlite3'
        with self.db() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS settings(k TEXT PRIMARY KEY,v TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS sources(id TEXT PRIMARY KEY,config TEXT NOT NULL,status TEXT DEFAULT '待验证',
              checked TEXT,success TEXT,error TEXT,etag TEXT,modified TEXT,entries INTEGER DEFAULT 0,next_due TEXT);
            CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY,title TEXT,domain TEXT,region TEXT,published TEXT,
              first_seen TEXT,updated TEXT,analysis TEXT,analysis_status TEXT DEFAULT '待分析',analyzed_hash TEXT);
            CREATE TABLE IF NOT EXISTS articles(id TEXT PRIMARY KEY,event_id TEXT NOT NULL,source_id TEXT NOT NULL,
              url TEXT UNIQUE,title TEXT,published TEXT,first_seen TEXT,updated TEXT,body TEXT,excerpt TEXT,
              content_hash TEXT,coverage TEXT,tier TEXT,author TEXT);
            CREATE TABLE IF NOT EXISTS article_versions(id INTEGER PRIMARY KEY,article_id TEXT,recorded TEXT,data TEXT);
            CREATE TABLE IF NOT EXISTS event_versions(id INTEGER PRIMARY KEY,event_id TEXT,recorded TEXT,data TEXT);
            CREATE TABLE IF NOT EXISTS analysis_attempts(id INTEGER PRIMARY KEY,event_id TEXT,recorded TEXT,error TEXT,response TEXT);
            CREATE TABLE IF NOT EXISTS merges(source_id TEXT PRIMARY KEY,target_id TEXT,recorded TEXT);
            CREATE TABLE IF NOT EXISTS entities(event_id TEXT,kind TEXT,name TEXT,identity TEXT,
              UNIQUE(event_id,kind,name,identity));
            CREATE TABLE IF NOT EXISTS marks(event_id TEXT PRIMARY KEY,star INTEGER DEFAULT 0,later INTEGER DEFAULT 0,read_at TEXT);
            CREATE TABLE IF NOT EXISTS follows(term TEXT PRIMARY KEY,created TEXT);
            CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,started TEXT,finished TEXT,status TEXT,summary TEXT);
            CREATE TABLE IF NOT EXISTS usage(day TEXT PRIMARY KEY,events INTEGER DEFAULT 0,input INTEGER DEFAULT 0,output INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS crawl_queue(source_id TEXT,url TEXT,payload TEXT,queued TEXT,done INTEGER DEFAULT 0,PRIMARY KEY(source_id,url));
            CREATE INDEX IF NOT EXISTS ix_event_time ON events(published);
            CREATE INDEX IF NOT EXISTS ix_article_event ON articles(event_id);
            CREATE INDEX IF NOT EXISTS ix_article_hash ON articles(content_hash);
            CREATE INDEX IF NOT EXISTS ix_entity_name ON entities(name);
            ''')
            for s in SOURCES:
                db.execute('INSERT OR IGNORE INTO sources(id,config,status) VALUES(?,?,?)',
                           (s['id'],dumps(s),'候选·未接入' if s['kind']=='candidate' else '待验证'))
            for k,v in DEFAULTS.items():
                db.execute('INSERT OR IGNORE INTO settings VALUES(?,?)',(k,dumps(v)))

    @contextmanager
    def db(self):
        conn=sqlite3.connect(self.path, timeout=20)
        conn.row_factory=sqlite3.Row
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('PRAGMA busy_timeout=20000')
        try:
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def settings(self):
        with self.db() as db:
            return {r['k']:json.loads(r['v']) for r in db.execute('SELECT * FROM settings')}

    def configure(self, values):
        limits={'daily_events':(0,200),'daily_input_tokens':(0,2000000),'daily_output_tokens':(0,400000),
                'max_documents':(1,100),'feed_minutes':(15,1440),'page_minutes':(30,1440),'research_minutes':(720,10080)}
        for k,v in values.items():
            if k not in DEFAULTS: raise ValueError('未知设置')
            if k in limits and (type(v)!=int or not limits[k][0]<=v<=limits[k][1]): raise ValueError('设置超出范围')
            if k.endswith('enabled') and type(v)!=bool: raise ValueError('开关必须为布尔值')
            if k=='reasoning_effort' and v not in ('low','high','max'): raise ValueError('未知推理强度')
        with self.db() as db:
            db.executemany('UPDATE settings SET v=? WHERE k=?',[(dumps(v),k) for k,v in values.items()])

    def sources(self):
        with self.db() as db:
            return [{**dict(r), 'config':json.loads(r['config'])} for r in db.execute('SELECT * FROM sources ORDER BY id')]

    def source_state(self, key, **values):
        allowed={'status','checked','success','error','etag','modified','entries','next_due'}
        if not values.keys()<=allowed: raise ValueError('未知来源字段')
        with self.db() as db:
            db.execute('UPDATE sources SET '+','.join(k+'=?' for k in values)+' WHERE id=?',[*values.values(),key])

    def enable_source(self, key, enabled):
        with self.db() as db:
            row=db.execute('SELECT config FROM sources WHERE id=?',(key,)).fetchone()
            cfg=json.loads(row[0])
            if cfg['kind']=='candidate': raise ValueError('候选源尚无适配器')
            cfg['enabled']=bool(enabled)
            db.execute('UPDATE sources SET config=? WHERE id=?',(dumps(cfg),key))

    def ingest(self, article):
        a=dict(article); stamp=now()
        fingerprint=digest(re.sub(r'\s+','',a.get('body') or (a['title']+' '+a.get('excerpt',''))))
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            old=db.execute('SELECT * FROM articles WHERE url=?',(a['url'],)).fetchone()
            if old and old['content_hash']==fingerprint and old['title']==a['title'] and old['published']==a.get('published') and old['excerpt']==a.get('excerpt','')[:6000]:
                return old['event_id'], False
            # Exact reprints share an event, but every publisher and revision remains visible.
            duplicate=db.execute('SELECT event_id FROM articles WHERE content_hash=?',(fingerprint,)).fetchone()
            eid=old['event_id'] if old else (duplicate[0] if duplicate else uuid.uuid4().hex[:16])
            aid=old['id'] if old else uuid.uuid4().hex[:16]
            first=old['first_seen'] if old else stamp
            record=dict(id=aid,event_id=eid,source_id=a['source_id'],url=a['url'],title=a['title'],published=a.get('published'),
                        first_seen=first,updated=stamp,body=a.get('body','')[:60000],excerpt=a.get('excerpt','')[:6000],
                        content_hash=fingerprint,coverage=a.get('coverage','仅摘要'),tier=a['tier'],author=a.get('author',''))
            db.execute('INSERT INTO article_versions(article_id,recorded,data) VALUES(?,?,?)',(aid,stamp,dumps(record)))
            db.execute('INSERT OR REPLACE INTO articles('+','.join(record)+') VALUES('+','.join('?' for _ in record)+')',list(record.values()))
            db.execute('INSERT OR IGNORE INTO events(id,title,domain,region,published,first_seen,updated) VALUES(?,?,?,?,?,?,?)',
                       (eid,a['title'],a['domain'],a['region'],a.get('published'),stamp,stamp))
            db.execute("UPDATE events SET updated=?,analysis_status='待分析' WHERE id=?",(stamp,eid))
            return eid,True

    def event(self, eid, as_of=None):
        with self.db() as db:
            alias=db.execute('SELECT * FROM merges WHERE source_id=?',(eid,)).fetchone()
            if as_of and alias and alias['recorded']<=as_of:return self.event(alias['target_id'],as_of)
            row=db.execute('SELECT * FROM events WHERE id=?',(eid,)).fetchone()
            if not row: return None
            item=dict(row)
            articles=[dict(r) for r in db.execute('SELECT * FROM articles WHERE event_id=? ORDER BY first_seen',(eid,))]
            versions=[dict(r) for r in db.execute('SELECT * FROM event_versions WHERE event_id=? ORDER BY id',(eid,))]
            revisions=[dict(r) for r in db.execute('SELECT v.* FROM article_versions v JOIN articles a ON a.id=v.article_id WHERE a.event_id=? OR json_extract(v.data,\'$.event_id\')=? ORDER BY v.id',(eid,eid))]
            if as_of:
                # Knowledge-time boundary: current revisions must never leak into historical answers.
                latest={}
                visible_ids={eid}|{r[0] for r in db.execute('SELECT source_id FROM merges WHERE target_id=? AND recorded<=?',(eid,as_of))}
                for rev in revisions:
                    data=json.loads(rev['data'])
                    if data['event_id'] in visible_ids and rev['recorded']<=as_of and data.get('published') and data['published']<=as_of:
                        latest[rev['article_id']]=data
                articles=list(latest.values())
                versions=[v for v in versions if v['recorded']<=as_of]
                revisions=[v for v in revisions if v['recorded']<=as_of]
                if not articles: return None
                item['analysis']=versions[-1]['data'] if versions else None
                item['updated']=max(a['updated'] for a in articles)
                original=db.execute('SELECT config FROM sources WHERE id=?',(articles[0]['source_id'],)).fetchone()
                origin=json.loads(original[0]) if original else {}
                historical_analysis=json.loads(item['analysis']) if item['analysis'] else {}
                item['domain']=historical_analysis.get('domain',origin.get('domain',item['domain']))
                item['region']=origin.get('region',item['region'])
                item['title']=articles[0]['title']
                item['analysis_status']='已分析' if historical_analysis else '待分析'
            item['analysis']=json.loads(item['analysis']) if item.get('analysis') else None
            item['articles']=articles
            item['versions']=[{'recorded':v['recorded'],'analysis':json.loads(v['data'])} for v in versions]
            item['revisions']=[{'recorded':r['recorded'],'article':json.loads(r['data'])} for r in revisions]
            mark=db.execute('SELECT * FROM marks WHERE event_id=?',(eid,)).fetchone()
            item['marks']=dict(mark) if mark else {}
            return item

    def search(self, query='', filters=None, as_of=None, limit=100):
        filters=filters or {}; clauses=[] if as_of else ["e.analysis_status NOT LIKE '已合并:%'"]; params=[]
        for key in ('domain','region'):
            if filters.get(key) and not as_of: clauses.append('e.'+key+'=?'); params.append(filters[key])
        if filters.get('since'): clauses.append('e.published>=?'); params.append(filters['since'])
        if as_of: clauses.extend(['e.first_seen<=?','e.published<=?']); params.extend([as_of,as_of])
        if as_of:
            clauses.append('NOT EXISTS(SELECT 1 FROM merges m WHERE m.source_id=e.id AND m.recorded<=?)');params.append(as_of)
        if filters.get('tier') and not as_of:
            clauses.append('EXISTS(SELECT 1 FROM articles a WHERE a.event_id=e.id AND a.tier=?)'); params.append(filters['tier'])
        if query and not as_of:
            # LIKE is bound and escaped; works for one/two-character Chinese names too.
            q='%'+query.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')+'%'
            clauses.append("(e.title LIKE ? ESCAPE '\\' OR e.analysis LIKE ? ESCAPE '\\' OR EXISTS(SELECT 1 FROM articles a WHERE a.event_id=e.id AND (a.title LIKE ? ESCAPE '\\' OR a.excerpt LIKE ? ESCAPE '\\' OR a.body LIKE ? ESCAPE '\\')))" )
            params.extend([q]*5)
        sql='SELECT e.* FROM events e'+(' WHERE '+' AND '.join(clauses) if clauses else '')+' ORDER BY e.published DESC,e.updated DESC LIMIT ?'
        with self.db() as db:
            rows=[dict(r) for r in db.execute(sql,[*params,max(1,min(1000,limit))])]
            marks={r['event_id']:dict(r) for r in db.execute('SELECT * FROM marks')}
        out=[]
        for r in rows:
            r['analysis']=json.loads(r['analysis']) if r['analysis'] else None
            if as_of:
                historical=self.event(r['id'],as_of)
                if not historical: continue
                r.update(analysis=historical['analysis'],updated=historical['updated'],title=historical['articles'][0]['title'],domain=historical['domain'],region=historical['region'],analysis_status=historical['analysis_status'])
                if any(filters.get(k) and filters[k]!=r[k] for k in ('domain','region')):continue
                if filters.get('tier') and not any(a['tier']==filters['tier'] for a in historical['articles']):continue
                if query and query.casefold() not in dumps(historical).casefold(): continue
            r['marks']=marks.get(r['id'],{})
            if filters.get('marked') and not r['marks'].get(filters['marked']): continue
            out.append(r)
        return out

    def save_analysis(self,eid,analysis,fingerprint):
        stamp=now()
        with self.db() as db:
            db.execute('UPDATE events SET analysis=?,analysis_status=?,analyzed_hash=?,domain=? WHERE id=?',
                       (dumps(analysis),'已分析',fingerprint,analysis['domain'],eid))
            db.execute('INSERT INTO event_versions(event_id,recorded,data) VALUES(?,?,?)',(eid,stamp,dumps(analysis)))
            db.execute('DELETE FROM entities WHERE event_id=?',(eid,))
            for entity in analysis.get('entities',[]):
                db.execute('INSERT OR IGNORE INTO entities VALUES(?,?,?,?)',(eid,entity['kind'],entity['name'],entity['identity']))

    def mark(self,eid,kind,value=True):
        if kind not in ('star','later','read_at'): raise ValueError('未知标记')
        with self.db() as db:
            db.execute('INSERT OR IGNORE INTO marks(event_id) VALUES(?)',(eid,))
            db.execute('UPDATE marks SET '+kind+'=? WHERE event_id=?',(now() if kind=='read_at' and value else int(value),eid))

    def follows(self):
        with self.db() as db: return [r[0] for r in db.execute('SELECT term FROM follows ORDER BY created')]

    def follow(self,term,remove=False):
        term=term.strip()[:100]
        if not term: return
        with self.db() as db:
            db.execute('DELETE FROM follows WHERE term=?' if remove else 'INSERT OR IGNORE INTO follows VALUES(?,?)',
                       (term,) if remove else (term,now()))

    def usage(self):
        with self.db() as db:
            row=db.execute('SELECT * FROM usage WHERE day=?',(now()[:10],)).fetchone()
        return dict(row) if row else dict(day=now()[:10],events=0,input=0,output=0)

    def reserve(self,input_tokens,output_tokens):
        settings=self.settings(); day=now()[:10]
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('INSERT OR IGNORE INTO usage(day) VALUES(?)',(day,))
            u=db.execute('SELECT * FROM usage WHERE day=?',(day,)).fetchone()
            if (u['events']>=settings['daily_events'] or u['input']+input_tokens>settings['daily_input_tokens']
                    or u['output']+output_tokens>settings['daily_output_tokens']): return None
            db.execute('UPDATE usage SET events=events+1,input=input+?,output=output+? WHERE day=?',(input_tokens,output_tokens,day))
        return (day,input_tokens,output_tokens)

    def settle(self,reservation,usage):
        # Unknown/failed usage retains reservation conservatively, including reasoning tokens.
        if not usage: return
        day,inp,out=reservation
        if 'prompt_tokens' not in usage or 'completion_tokens' not in usage: return
        with self.db() as db:
            db.execute('UPDATE usage SET input=input+?,output=output+? WHERE day=?',
                       (max(0,int(usage['prompt_tokens']))-inp,max(0,int(usage['completion_tokens']))-out,day))

    def jobs(self):
        with self.db() as db: return [dict(r) for r in db.execute('SELECT * FROM jobs ORDER BY started DESC LIMIT 15')]
