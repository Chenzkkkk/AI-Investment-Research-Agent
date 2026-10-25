from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
import math

from ..common import DataError, symbol


class Source(str, Enum):
    EASTMONEY_WEB_SIM = "EASTMONEY_WEB_SIM"
    DONGCAI_GM_SIM = "DONGCAI_GM_SIM"
    CHOICE_PORTFOLIO = "CHOICE_PORTFOLIO"
    LOCAL_PAPER = "LOCAL_PAPER"
    REAL_BROKER = "REAL_BROKER"


class Status(str, Enum):
    NOT_CONFIGURED = "NOT_CONFIGURED"
    SYNC_FAILED = "SYNC_FAILED"
    SYNC_PENDING = "SYNC_PENDING"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    ACCOUNT_NOT_FOUND = "ACCOUNT_NOT_FOUND"
    CONNECTED_UNVERIFIED = "CONNECTED_UNVERIFIED"
    CONNECTED_VERIFIED = "CONNECTED_VERIFIED"
    SYNC_STALE = "SYNC_STALE"
    MANUAL_IMPORT_ONLY = "MANUAL_IMPORT_ONLY"


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def timestamp(value):
    if not isinstance(value, str):
        raise DataError("INVALID_TIMESTAMP", "时间必须为含时区的 ISO 时间。")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None:
            raise ValueError()
        return result
    except ValueError:
        raise DataError("INVALID_TIMESTAMP", "时间必须为含时区的 ISO 时间。") from None


def numeric(value, *, nonnegative=False):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise DataError("INVALID_ACCOUNT_NUMBER", "账户数值必须为有限数字或 null，不接受推测值。")
    if nonnegative and value < 0:
        raise DataError("INVALID_ACCOUNT_NUMBER", "该账户字段不可为负数。")
    return float(value)


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest()


def masked(value):
    return value[:2] + "***" + value[-3:] if value and len(value) > 6 else "***" if value else "unavailable"


@dataclass
class Target:
    key: str
    source: str
    name: str
    account_id: str | None = None
    approval: str = "UNKNOWN"
    interval_seconds: int = 300
    stale_seconds: int = 600
    auto_sync: bool = False

    def __post_init__(self):
        Source(self.source)
        if self.source in (Source.REAL_BROKER.value, Source.LOCAL_PAPER.value):
            raise DataError("SOURCE_BLOCKED", "此同步层不接入实盘或本地研究账本。")
        if not self.key or not 60 <= self.interval_seconds <= 86400 or not 60 <= self.stale_seconds <= 86400:
            raise DataError("INVALID_TARGET", "同步配置无效，间隔及过期阈值至少60秒。")


@dataclass
class Snapshot:
    source: str
    account_id: str
    account_name: str | None
    captured_at: str
    source_data_at: str | None = None
    cash: dict = field(default_factory=dict)
    positions: list[dict] | None = None
    orders: list[dict] | None = None
    executions: list[dict] | None = None
    transport: str = "manual_import"
    scope: str = "unknown"
    warnings: list[str] = field(default_factory=list)

    def dict(self):
        return asdict(self)

    def content_hash(self):
        # Capture time changes on every poll, so only stable source data forms the verification revision.
        return fingerprint({k: v for k, v in self.dict().items() if k not in ("captured_at", "warnings")})


CASH_FIELDS = {"total_assets", "available", "frozen", "market_value", "cash_balance", "floating_pnl", "total_pnl", "return_pct"}
POSITION_FIELDS = {"account_id", "symbol", "name", "quantity", "sellable", "cost_price", "cost_amount", "price", "market_value", "floating_pnl", "updated_at"}
ORDER_FIELDS = {"account_id", "order_id", "symbol", "side", "status", "quantity", "filled_quantity", "price", "created_at", "updated_at"}
EXECUTION_FIELDS = {"account_id", "execution_id", "order_id", "symbol", "side", "quantity", "price", "amount", "fees", "executed_at", "execution_type"}


def validate_snapshot(snapshot, target):
    if snapshot.source != target.source or not isinstance(snapshot.account_id, str) or not snapshot.account_id.strip() or len(snapshot.account_id) > 200:
        raise DataError("ACCOUNT_IDENTITY_MISMATCH", "来源或官方唯一账户ID缺失/不匹配；不接受展示名称代替ID。")
    if target.account_id and snapshot.account_id != target.account_id:
        raise DataError("ACCOUNT_IDENTITY_MISMATCH", "数据来自另一个账户，已拒绝同步。")
    if snapshot.transport not in ("manual_import", "official_sdk", "approved_web_read"):
        raise DataError("INVALID_TRANSPORT", "账户数据通道未识别。")
    if snapshot.account_name is not None and (not isinstance(snapshot.account_name,str) or len(snapshot.account_name)>200):
        raise DataError("INVALID_ACCOUNT_SCHEMA", "账户名称格式无效。")
    if not isinstance(snapshot.cash,dict):
        raise DataError("INVALID_ACCOUNT_SCHEMA", "资金必须为规范对象，未知字段用null。")
    captured = timestamp(snapshot.captured_at)
    if (captured - datetime.now(timezone.utc)).total_seconds() > 60:
        raise DataError("FUTURE_ACCOUNT_DATA", "账户采集时间位于未来。")
    if snapshot.source_data_at:
        if (timestamp(snapshot.source_data_at) - datetime.now(timezone.utc)).total_seconds() > 60:
            raise DataError("FUTURE_ACCOUNT_DATA", "账户来源时间位于未来。")
    if set(snapshot.cash) - CASH_FIELDS:
        raise DataError("INVALID_ACCOUNT_SCHEMA", "资金字段超出允许范围。")
    snapshot.cash = {key: numeric(snapshot.cash.get(key), nonnegative=key in {"available", "frozen", "market_value", "cash_balance"}) for key in sorted(CASH_FIELDS)}
    for rows, allowed in ((snapshot.positions, POSITION_FIELDS), (snapshot.orders, ORDER_FIELDS), (snapshot.executions, EXECUTION_FIELDS)):
        if rows is None:
            continue
        if not isinstance(rows, list) or len(rows) > 20000:
            raise DataError("INVALID_ACCOUNT_SCHEMA", "账户明细必须为有界列表或null（unavailable）。")
        for row in rows:
            if not isinstance(row, dict) or set(row) - allowed:
                raise DataError("INVALID_ACCOUNT_SCHEMA", "账户明细含未知字段。")
            if row.get("account_id") != snapshot.account_id:
                raise DataError("ACCOUNT_IDENTITY_MISMATCH", "明细账户ID缺失或属于其他账户，拒绝混合。")
            if row.get("symbol") is not None:
                if not isinstance(row["symbol"], str):
                    raise DataError("INVALID_ACCOUNT_SCHEMA", "证券代码格式无效。")
                row["symbol"] = symbol(row["symbol"])
            for key in set(row) & {"order_id","execution_id","name"}:
                if row[key] is not None and (not isinstance(row[key],str) or len(row[key])>200):
                    raise DataError("INVALID_ACCOUNT_SCHEMA", "明细标识格式无效。")
            for key in set(row) & {"side","status","execution_type"}:
                if row[key] is not None and (type(row[key]) not in (str,int) or len(str(row[key]))>40):
                    raise DataError("INVALID_ACCOUNT_SCHEMA", "状态或方向格式无效。")
            for key in set(row) & {"created_at","updated_at","executed_at"}:
                if row[key] is not None:
                    timestamp(row[key])
            for key in set(row) & {"quantity", "sellable", "cost_price", "cost_amount", "price", "market_value", "floating_pnl", "filled_quantity", "amount", "fees"}:
                row[key] = numeric(row[key], nonnegative=key not in {"floating_pnl"})
            if row.get("quantity") is not None:
                for key in ("sellable", "filled_quantity"):
                    if row.get(key) is not None and row[key] > row["quantity"]:
                        raise DataError("ACCOUNT_INCONSISTENT", "可卖或成交数量大于总数量，拒绝快照。")
    return snapshot
