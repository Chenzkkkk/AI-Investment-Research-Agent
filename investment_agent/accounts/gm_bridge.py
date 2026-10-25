"""Local request queue, never loads a trading SDK into Streamlit."""
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from .models import timestamp, utc_now
from ..common import DataError


class GMBridgeProvider:
    source="DONGCAI_GM_SIM"

    def __init__(self, root):
        self.path=Path(root)/"data/accounts/official.sqlite3"

    @property
    def live_ready(self):
        if not self.path.exists():
            return False
        try:
            with sqlite3.connect(self.path,timeout=2) as db:
                rows=db.execute("SELECT heartbeat FROM gm_workers").fetchall()
            return any((datetime.now(timezone.utc)-timestamp(row[0])).total_seconds()<10 for row in rows)
        except (sqlite3.Error,DataError):
            return False

    def ready_for(self,target):
        try:
            with sqlite3.connect(self.path,timeout=2) as db:
                row=db.execute("SELECT heartbeat,account FROM gm_workers WHERE target=?",(target.key,)).fetchone()
            return bool(row and row[1]==target.account_id and (datetime.now(timezone.utc)-timestamp(row[0])).total_seconds()<10)
        except (sqlite3.Error,DataError):
            return False

    def fetch(self,target):
        with sqlite3.connect(self.path,timeout=5) as db:
            row=db.execute("SELECT heartbeat,account FROM gm_workers WHERE target=?",(target.key,)).fetchone()
            if not row or row[1]!=target.account_id or (datetime.now(timezone.utc)-timestamp(row[0])).total_seconds()>10:
                raise DataError("GM_CONTEXT_REQUIRED","未检测到本账户的已确认仿真只读Worker；未启动SDK或新建官方账户。")
            db.execute("INSERT OR REPLACE INTO gm_requests VALUES(?,?)",(target.key,utc_now()))
        raise DataError("SYNC_PENDING","只读刷新请求已入本地队列，等待官方SDK上下文读取；请求入队不代表同步成功。")


class GMContextWorker:
    """Called only from an existing official GM strategy context, after user setup."""
    def __init__(self,root,target_key,api,binding):
        from .dongcai_gm import DongcaiGMProvider
        from .sync_service import AccountSyncService
        self.provider=DongcaiGMProvider(api,terminal_binding=binding)
        self.service=AccountSyncService(root,providers={"DONGCAI_GM_SIM":self.provider})
        self.target_key=target_key
        self.target=self.service.target(target_key)
        if not self.target or self.target.source!="DONGCAI_GM_SIM" or self.target.approval!="APPROVED" or not self.provider.live_ready or self.target.account_id!=binding.get("terminal_account_id"):
            raise DataError("GM_CONTEXT_REQUIRED","必须先由本人确认已有GM仿真账户、唯一ID、审批和策略关联，拒绝默认账户。")
        self.last_query=None

    def tick(self, *, startup=False):
        target=self.service.target(self.target_key)
        stamp=utc_now()
        with self.service.db() as db:
            db.execute("INSERT OR REPLACE INTO gm_workers VALUES(?,?,?)",(target.key,target.account_id,stamp))
            request=db.execute("SELECT requested_at FROM gm_requests WHERE target=?",(target.key,)).fetchone()
        elapsed=(datetime.now(timezone.utc)-timestamp(self.last_query)).total_seconds() if self.last_query else float("inf")
        should_query=startup or bool(request) or target.auto_sync and elapsed>=target.interval_seconds
        if not should_query or elapsed<60:
            return self.service.view(target.key)
        if request:
            with self.service.db() as db:
                db.execute("DELETE FROM gm_requests WHERE target=? AND requested_at=?",(target.key,request[0]))
        self.last_query=stamp
        return self.service.sync(target.key)
