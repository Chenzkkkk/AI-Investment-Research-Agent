"""Read-only interfaces shared by Streamlit and ResearchAgent."""
from datetime import datetime, timedelta
from .store import Store, TZ, now
from .collect import date


class IntelligenceService:
    def __init__(self,root): self.store=Store(root)

    def boundary(self,as_of):
        if not as_of: return None
        parsed=date(as_of)
        if not parsed: raise ValueError('as_of 必须是有效且不晚于当前的日期时间')
        return parsed

    def search_intelligence(self,query='',filters=None,as_of=None):
        boundary=self.boundary(as_of)
        rows=self.store.search(query,filters,boundary,30)
        data=[]
        for row in rows:
            event=self.store.event(row['id'],boundary)
            if not event: continue
            data.append({k:event[k] for k in ('id','title','domain','region','published','updated','analysis_status','analysis')}|
                        {'sources':[{k:a[k] for k in ('id','title','url','published','coverage','tier')} for a in event['articles']]})
        return {'ok':True,'source':'local_intelligence','data':data,'coverage':self.coverage(),
                'warning':'本地已采集资料，不代表全网覆盖。公开时间未知的材料不能用于历史时点结论。','as_of':boundary or now()}

    def get_intelligence_event(self,event_id,as_of=None):
        event=self.store.event(event_id,self.boundary(as_of))
        if not event: return {'ok':False,'error':'事件不存在或在指定时点尚不可见'}
        if not as_of and event['analysis_status'].startswith('已合并:'):
            return self.get_intelligence_event(event['analysis_status'].split(':',1)[1],as_of)
        # Give the model bounded primary evidence, not an unbounded archive dump.
        event.pop('revisions',None)
        for a in event['articles']: a['body']=a['body'][:8000]
        event['versions']=event['versions'][-5:]
        return {'ok':True,'source':'local_intelligence','data':event,'coverage':self.coverage()}

    def get_topic_brief(self,topic,period=7):
        if period not in (1,7,30): raise ValueError('period 仅支持1、7、30天')
        since=(datetime.now(TZ)-timedelta(days=period)).isoformat(timespec='seconds')
        result=self.search_intelligence(topic,{'since':since})
        result['brief_mode']='读取已保存事实及分析；调用方可据此撰写中文简报，不触发后台模型'
        return result

    def coverage(self):
        rows=self.store.sources()
        return [{'source':r['config']['name'],'domain':r['config']['domain'],'status':r['status'],
                 'last_success':r['success'],'enabled':r['config']['enabled']} for r in rows]
