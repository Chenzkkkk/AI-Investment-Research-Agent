from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import uuid

from .models import Source, Status, Target, Snapshot, fingerprint, masked, timestamp, utc_now, validate_snapshot
from .validation import consistency, compare_official
from .budget import BudgetPolicy, budget_analysis
from .eastmoney_web import EastmoneyWebProvider
from .gm_bridge import GMBridgeProvider
from .choice_provider import ChoiceAccountProvider
from ..common import DataError


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


class AccountSyncService:
    def __init__(self, root, providers=None):
        self.root = Path(root)
        self.path = self.root / "data/accounts/official.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.providers = providers if providers is not None else {
            Source.EASTMONEY_WEB_SIM.value: EastmoneyWebProvider(),
            Source.DONGCAI_GM_SIM.value: GMBridgeProvider(root),
            Source.CHOICE_PORTFOLIO.value: ChoiceAccountProvider(),
        }
        with self.db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS targets(key TEXT PRIMARY KEY, config TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS snapshots(target TEXT PRIMARY KEY, data TEXT NOT NULL, revision TEXT NOT NULL, received_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS states(target TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS policies(target TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS reviews(target TEXT PRIMARY KEY, revision TEXT NOT NULL, passed INTEGER NOT NULL, reviewed_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS executions(source TEXT, account TEXT, day TEXT, id TEXT, data TEXT NOT NULL,
                    PRIMARY KEY(source,account,day,id));
                CREATE TABLE IF NOT EXISTS order_history(source TEXT, account TEXT, day TEXT, id TEXT, data TEXT NOT NULL,
                    PRIMARY KEY(source,account,day,id));
                CREATE TABLE IF NOT EXISTS sync_audit(id TEXT PRIMARY KEY,target TEXT,created_at TEXT,event TEXT,detail TEXT);
                CREATE TABLE IF NOT EXISTS gm_workers(target TEXT PRIMARY KEY,account TEXT,heartbeat TEXT);
                CREATE TABLE IF NOT EXISTS gm_requests(target TEXT PRIMARY KEY,requested_at TEXT);
            """)
            # A local target record is not an official account creation, nor an imported asset balance.
            default = Target("web-primary", Source.EASTMONEY_WEB_SIM.value, "官方模拟组合")
            db.execute("INSERT OR IGNORE INTO targets VALUES(?,?)", (default.key, encode(asdict(default))))

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def targets(self):
        with self.db() as db:
            return [Target(**json.loads(row["config"])) for row in db.execute("SELECT config FROM targets ORDER BY key")]

    def target(self, key):
        return next((t for t in self.targets() if t.key == key), None)

    def save_target(self, target):
        target.__post_init__()
        prior = self.target(target.key)
        snapshot = self.snapshot(target.key)
        if prior and (prior.source != target.source or prior.account_id and prior.account_id != target.account_id):
            raise DataError("TARGET_IDENTITY_IMMUTABLE", "已有目标来源或账户ID不能原地更换，请新建独立的本地连接配置。")
        if snapshot and target.account_id != snapshot.account_id:
            raise DataError("TARGET_IDENTITY_IMMUTABLE", "配置ID必须与已导入快照一致。")
        with self.db() as db:
            db.execute("INSERT OR REPLACE INTO targets VALUES(?,?)", (target.key, encode(asdict(target))))

    def snapshot(self, key):
        with self.db() as db:
            row = db.execute("SELECT data FROM snapshots WHERE target=?", (key,)).fetchone()
        return Snapshot(**json.loads(row[0])) if row else None

    def policy(self, key):
        with self.db() as db:
            row = db.execute("SELECT data FROM policies WHERE target=?", (key,)).fetchone()
        return BudgetPolicy(**json.loads(row[0])) if row else BudgetPolicy()

    def save_policy(self, key, policy):
        policy.__post_init__()
        with self.db() as db:
            db.execute("INSERT OR REPLACE INTO policies VALUES(?,?)", (key, encode(asdict(policy))))

    def _state(self, key, code, message, *, last_success=None, received_at=None):
        with self.db() as db:
            previous = db.execute("SELECT data FROM states WHERE target=?", (key,)).fetchone()
            old = json.loads(previous[0]) if previous else {}
            state = {"connection_status": code, "message": message, "last_attempt_at": utc_now(),
                     "last_success_at": last_success or old.get("last_success_at"),
                     "last_received_at": received_at or old.get("last_received_at")}
            db.execute("INSERT OR REPLACE INTO states VALUES(?,?)", (key, encode(state)))
            db.execute("INSERT INTO sync_audit VALUES(?,?,?,?,?)", (uuid.uuid4().hex,key,utc_now(),code,message))
        return self.view(key)

    def ready(self, key):
        target = self.target(key)
        provider = self.providers.get(target.source) if target else None
        return bool(provider and (provider.ready_for(target) if hasattr(provider,"ready_for") else getattr(provider,"live_ready",False)))

    def sync(self, key, *, automatic=False):
        target = self.target(key)
        if not target:
            raise DataError("ACCOUNT_NOT_FOUND", "未找到本地账户连接配置。")
        if target.approval == "PENDING_APPROVAL":
            return self._state(key, Status.PENDING_APPROVAL.value, "审批尚未完成；未发起账户请求。")
        if automatic:
            view = self.view(key)
            if not target.auto_sync or not self.ready(key):
                return view
            if view.get("last_attempt_at") and (datetime.now(timezone.utc)-timestamp(view["last_attempt_at"])).total_seconds() < target.interval_seconds:
                return view
        provider = self.providers.get(target.source)
        if not provider:
            return self._state(key, Status.NOT_CONFIGURED.value, "未配置该来源的只读Provider。")
        try:
            return self.ingest(key, provider.fetch(target), live=True)
        except Exception as exc:
            code = exc.code if isinstance(exc, DataError) else "PROVIDER_EXCEPTION"
            status = code if code in {s.value for s in Status} else Status.NOT_CONFIGURED.value if code == "GM_CONTEXT_REQUIRED" else Status.SYNC_FAILED.value
            message = str(exc) if isinstance(exc, DataError) else "账户读取失败：" + type(exc).__name__ + "；原始异常未记录。"
            return self._state(key, status, code + "：" + message)

    def ingest(self, key, snapshot, *, live=False):
        target = self.target(key)
        if not target:
            raise DataError("ACCOUNT_NOT_FOUND", "未找到本地连接配置。")
        if live != (snapshot.transport != "manual_import"):
            raise DataError("TRANSPORT_MISMATCH", "手工导入不可冒充SDK或自动网页同步。")
        snapshot = validate_snapshot(snapshot, target)
        consistency(snapshot)
        prior = self.snapshot(key)
        if prior and (prior.account_id != snapshot.account_id or timestamp(snapshot.captured_at) < timestamp(prior.captured_at)):
            raise DataError("ACCOUNT_REVISION_REJECTED", "账户ID变化或倒序快照，已保留原数据。")
        missing_ids = 0
        for field, identity in (("orders", "order_id"), ("executions", "execution_id")):
            rows = getattr(snapshot, field)
            if rows is None:
                continue
            unique = {}
            unknown = []
            for row in rows:
                if not row.get(identity):
                    missing_ids += 1
                    unknown.append(row)
                    continue
                if row[identity] in unique and unique[row[identity]] != row:
                    raise DataError("ACCOUNT_EVENT_CONFLICT", "同一明细ID在本次读取中有冲突，不猜测覆盖顺序。")
                unique[row[identity]] = row
            setattr(snapshot, field, sorted(unique.values(), key=lambda r:str(r[identity])) + unknown)
        if missing_ids:
            snapshot.warnings.append("存在缺失唯一ID的明细：可查看快照，但不并入去重累计记录；禁止推测历史笔数。")
        revision, received = snapshot.content_hash(), utc_now()
        with self.db() as db:
            # Recheck under the same write transaction to prevent concurrent older polls overwriting newer ones.
            current = db.execute("SELECT data FROM snapshots WHERE target=?", (key,)).fetchone()
            if current:
                old = Snapshot(**json.loads(current[0]))
                if old.account_id != snapshot.account_id or timestamp(snapshot.captured_at) < timestamp(old.captured_at):
                    raise DataError("ACCOUNT_REVISION_REJECTED", "已有更新的账户快照，拒绝倒序写入。")
            for row in snapshot.executions or []:
                if not row.get("execution_id") or not row.get("executed_at"):
                    continue
                day = timestamp(row["executed_at"]).astimezone(timezone.utc).date().isoformat()
                identity = (snapshot.source, snapshot.account_id, day, str(row["execution_id"]))
                old = db.execute("SELECT data FROM executions WHERE source=? AND account=? AND day=? AND id=?", identity).fetchone()
                if old and json.loads(old[0]) != row:
                    raise DataError("ACCOUNT_EVENT_CONFLICT", "同一执行回报ID内容改变，需人工核对，不重复计入。")
                db.execute("INSERT OR IGNORE INTO executions VALUES(?,?,?,?,?)", (*identity, encode(row)))
            for row in snapshot.orders or []:
                if not row.get("order_id") or not row.get("created_at"):
                    continue
                day = timestamp(row["created_at"]).astimezone(timezone.utc).date().isoformat()
                identity = (snapshot.source,snapshot.account_id,day,str(row["order_id"]))
                old = db.execute("SELECT data FROM order_history WHERE source=? AND account=? AND day=? AND id=?", identity).fetchone()
                if old:
                    old_row = json.loads(old[0])
                    if row.get("filled_quantity") is not None and old_row.get("filled_quantity") is not None and row["filled_quantity"] < old_row["filled_quantity"]:
                        raise DataError("ORDER_STATE_REGRESSION", "累计成交量倒退，快照已拒绝。")
                    if row.get("updated_at") and old_row.get("updated_at") and timestamp(row["updated_at"]) < timestamp(old_row["updated_at"]):
                        raise DataError("ORDER_STATE_REGRESSION", "委托状态时间倒退，快照已拒绝。")
                db.execute("INSERT OR REPLACE INTO order_history VALUES(?,?,?,?,?)", (*identity,encode(row)))
            db.execute("INSERT OR REPLACE INTO snapshots VALUES(?,?,?,?)", (key,encode(snapshot.dict()),revision,received))
        code = Status.MANUAL_IMPORT_ONLY.value if not live else Status.CONNECTED_UNVERIFIED.value
        return self._state(key, code, "已接收快照；缺失字段保持unavailable，身份与数据核验另计。",
                           last_success=snapshot.captured_at, received_at=received)

    def verify(self, key, evidence):
        snapshot = self.snapshot(key)
        if not snapshot:
            raise DataError("ACCOUNT_NOT_FOUND", "尚无账户快照可核对。")
        failures = compare_official(snapshot, evidence)
        with self.db() as db:
            db.execute("INSERT OR REPLACE INTO reviews VALUES(?,?,?,?)", (key,snapshot.content_hash(),int(not failures),utc_now()))
        return {"passed": not failures,"mismatches": failures,"status":self.view(key)["status"],
                "note":"本人逐项核对记录；手工导入仍为MANUAL_IMPORT_ONLY，不据此声称已连接。"}

    def view(self, key):
        target = self.target(key)
        if not target:
            raise DataError("ACCOUNT_NOT_FOUND", "账户连接配置不存在。")
        snapshot = self.snapshot(key)
        with self.db() as db:
            row = db.execute("SELECT data FROM states WHERE target=?", (key,)).fetchone()
            review = db.execute("SELECT revision,passed,reviewed_at FROM reviews WHERE target=?", (key,)).fetchone()
            count = db.execute("SELECT COUNT(*) FROM executions WHERE source=? AND account=?", (target.source,snapshot.account_id if snapshot else target.account_id)).fetchone()[0]
        default_status = Status.MANUAL_IMPORT_ONLY.value if target.source == Source.EASTMONEY_WEB_SIM.value else Status.NOT_CONFIGURED.value
        state = json.loads(row[0]) if row else {"connection_status": default_status,"message":"尚未读取官方账户数据。","last_success_at":None,"last_attempt_at":None,"last_received_at":None}
        status = state["connection_status"]
        verified = bool(snapshot and review and review[0] == snapshot.content_hash() and review[1])
        stale = bool(snapshot and (datetime.now(timezone.utc)-timestamp(snapshot.captured_at)).total_seconds() > target.stale_seconds)
        worker_lost = bool(snapshot and snapshot.transport=="official_sdk" and snapshot.scope.startswith("sdk_context_")
                           and isinstance(self.providers.get(target.source),GMBridgeProvider) and not self.ready(key))
        if target.approval == "PENDING_APPROVAL":
            status = Status.PENDING_APPROVAL.value
        elif stale or worker_lost:
            status = Status.SYNC_STALE.value
        elif snapshot and snapshot.transport != "manual_import" and verified and status == Status.CONNECTED_UNVERIFIED.value:
            status = Status.CONNECTED_VERIFIED.value
        if worker_lost:
            state["connection_status"] = Status.SYNC_FAILED.value
            state["message"] = "GM只读Worker心跳已失效，连续更新已中断；保留最后快照，不输出执行数量。"
        return {"key":key,"source":target.source,"account_name":snapshot.account_name if snapshot else target.name,
                "account_id_masked":masked(snapshot.account_id if snapshot else target.account_id),"approval":target.approval,
                **state,"status":status,"identity_and_data_review_passed":verified,"stale":stale or worker_lost,
                "transport":snapshot.transport if snapshot else None,"source_data_at":snapshot.source_data_at if snapshot else None,
                "live_provider_ready":self.ready(key),"known_execution_reports":count,
                "snapshot":snapshot.dict() if snapshot else None}

    def overview(self, key):
        """Explicitly selected context only; no names, account IDs, or individual order IDs sent to LLM."""
        view = self.view(key)
        snapshot = self.snapshot(key)
        risk = self.analyze(key)
        return {"source":view["source"],"kind":"official_account_snapshot","ok":bool(snapshot),
                "data_time":view["last_success_at"],"data":{"status":view["status"],"identity_reviewed":view["identity_and_data_review_passed"],
                "cash":snapshot.cash if snapshot else None,"position_count":len(snapshot.positions) if snapshot and snapshot.positions is not None else None,
                "positions":[{k:v for k,v in row.items() if k not in {"account_id","name"}} for row in (snapshot.positions or [])] if snapshot and snapshot.positions is not None else None,
                "budget":risk,"source_data_at":view["source_data_at"]},
                "metadata":{"research_only":True,"manual_import":view["transport"]=="manual_import"}}

    def analyze(self, key, *, code=None, price=None):
        view, snapshot = self.view(key), self.snapshot(key)
        result = budget_analysis(snapshot,self.policy(key),code=code,price=price,usable=view["status"]==Status.CONNECTED_VERIFIED.value)
        result["account_status"] = view["status"]
        if snapshot and snapshot.positions is not None and snapshot.cash.get("total_assets") and all(row.get("market_value") is not None for row in snapshot.positions):
            result["max_single_account_weight"] = max((row["market_value"]/snapshot.cash["total_assets"] for row in snapshot.positions),default=0)
        else:
            result["max_single_account_weight"] = None
        return result
