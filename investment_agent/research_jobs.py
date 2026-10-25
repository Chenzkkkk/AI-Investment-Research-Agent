"""One in-process worker: survives closing the browser, never duplicate-starts jobs."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace, asdict
import json
import threading
import time
from .common import DataError, now, safe
from .research_runtime import ResearchStore,ResearchBudget,ACTIVE_BUDGET


class ResearchJobs:
    def __init__(self,root):
        self.store=ResearchStore(root)
        self.pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='zh-research')
        self.futures={}; self.lock=threading.Lock()
        # On server restart there are no surviving in-process tasks. Keep checkpoints.
        with self.store.connect() as db:
            db.execute("UPDATE tasks SET status='interrupted',progress='服务已重启，可继续已保存的研究' WHERE status IN ('running','queued')")

    def start(self,tid,settings):
        with self.lock:
            if tid in self.futures and not self.futures[tid].done(): return
            if not self.store.claim(tid): return
            self.futures[tid]=self.pool.submit(self._run,tid,replace(settings))

    def _run(self,tid,settings):
        from .agent import ResearchAgent
        task=self.store.task(tid)
        profile=json.loads(task['profile'])
        settings.investment_style=profile.get('style','value')
        settings.reasoning_effort=profile.get('reasoning_effort','high')
        settings.max_rounds=12
        history=[{'role':m['role'],'content':m.get('content',m.get('result',{}).get('report',''))} for m in self.store.messages(task['conversation_id'])][:-1]
        secrets=(settings.deepseek_key,settings.mcp_key,settings.skills_key)
        budget=ResearchBudget(cancelled=lambda:self.store.task(tid)['status']=='cancelled')
        token=ACTIVE_BUDGET.set(budget)
        try:
            agent=ResearchAgent(settings)
            if task['checkpoint']:
                saved=json.loads(task['checkpoint'])
                if saved.get('graph'):
                    agent.initial_graph_state=saved['graph']
                    agent.coverage.items=saved.get('coverage',agent.coverage.items)
                # A resume receives the remaining budget, not a new allowance.
                for k in ('model_calls','input_tokens','output_tokens'):
                    setattr(budget,k,saved.get('budget',{}).get(k,0))
                agent.service.calls=saved.get('data_calls',0)
            def checkpoint(graph):
                self.store.checkpoint(tid,safe({'graph':graph,'coverage':agent.coverage.items,
                    'budget':{k:getattr(budget,k) for k in ('model_calls','input_tokens','output_tokens')},
                    'data_calls':agent.service.calls},secrets))
            agent.checkpoint_callback=checkpoint
            question=task['question']
            if any(v and v!='未提供' for v in profile.get('constraints',{}).values()):
                question+='\n用户自述投资约束（不是市场数据）：'+json.dumps(profile['constraints'],ensure_ascii=False)
            result=asyncio.run(agent.ask(question,progress=lambda s:self.store.progress(tid,s),history=history))
            result['budget_usage']={k:getattr(budget,k) for k in ('model_calls','input_tokens','output_tokens')}
            result['task_id']=tid
            partial=any(s in result['report'][:90] for s in ('预算','报告生成未完成','未生成完整结论','证据审阅未完成')) or self.store.task(tid)['status']=='cancelled'
            self.store.finish(tid,result,'partial' if partial else 'completed')
        except Exception as exc:
            text=safe(str(exc),secrets) if isinstance(exc,DataError) else type(exc).__name__+'：运行失败，已保存进度。'
            with self.store.connect() as db:
                db.execute("UPDATE tasks SET status='failed',progress=?,updated=? WHERE id=?",(text,time.time(),tid))
        finally: ACTIVE_BUDGET.reset(token)
