"""Run once from Task Scheduler. An OS lock prevents overlapping processes."""
import argparse
import asyncio
from contextlib import contextmanager
from pathlib import Path
import os
import sys
import uuid
from .store import Store,dumps,now
from .collect import collect
from .analyze import analyze_pending
from ..config import Settings


@contextmanager
def single_worker(directory):
    path=directory/'worker.lock'
    handle=open(path,'a+b')
    if path.stat().st_size==0:
        handle.write(b'0'); handle.flush()
    handle.seek(0)
    locked=False
    try:
        if os.name=='nt':
            import msvcrt
            try: msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1); locked=True
            except OSError: pass
        else:
            import fcntl
            try: fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB); locked=True
            except OSError: pass
        yield locked
    finally:
        if locked:
            handle.seek(0)
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)
            else:
                import fcntl
                fcntl.flock(handle,fcntl.LOCK_UN)
        handle.close()


def run(settings,force=False,no_model=False,limit=40,source_ids=None,analysis_only=False):
    store=Store(settings.root)
    with single_worker(store.directory) as acquired:
        if not acquired: return {'status':'already_running'}
        if not store.settings()['enabled']: return {'status':'paused'}
        job=uuid.uuid4().hex[:16]
        with store.db() as db:
            db.execute("UPDATE jobs SET status='上次进程中断',finished=? WHERE status='running'",(now(),))
            db.execute('INSERT INTO jobs(id,started,status) VALUES(?,?,?)',(job,now(),'running'))
        try:
            summary={} if analysis_only else collect(store,force,source_ids)
            summary['analysis']=[] if no_model else asyncio.run(analyze_pending(store,settings,limit))
            status='completed'
        except Exception as exc:
            summary={'error':type(exc).__name__}; status='failed'
        with store.db() as db:
            db.execute('UPDATE jobs SET finished=?,status=?,summary=? WHERE id=?',(now(),status,dumps(summary),job))
        return {'job':job,'status':status,**summary}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--force',action='store_true'); parser.add_argument('--no-model',action='store_true')
    parser.add_argument('--analysis-limit',type=int,default=40); parser.add_argument('--sources',default='')
    parser.add_argument('--analysis-only',action='store_true')
    args=parser.parse_args()
    settings=Settings.load()
    settings.enable_choice=False; settings.enable_miaoxiang=False
    value=run(settings,args.force,args.no_model,max(0,min(40,args.analysis_limit)),args.sources.split(',') if args.sources else None,args.analysis_only)
    print(dumps(value))


if __name__=='__main__': main()
