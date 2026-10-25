"""User-supplied official exports/captures, explicitly not a live connection."""
import json
from dataclasses import fields
from .models import Snapshot
from ..common import DataError


def read_manual(raw):
    if len(raw) > 8_000_000:
        raise DataError("ACCOUNT_FILE_TOO_LARGE", "账户文件最多8MB。")
    try:
        value = json.loads(raw)
        allowed = {item.name for item in fields(Snapshot)} - {"transport", "warnings"}
        if not isinstance(value, dict) or set(value) - allowed:
            raise ValueError()
        return Snapshot(**value, transport="manual_import", warnings=["本人导入的记录；不是官方自动同步，身份和数据仍须核对。"])
    except (ValueError, TypeError):
        raise DataError("ACCOUNT_FILE_SCHEMA", "请输入规范账户JSON；不要上传Token、Cookie、密码、原始登录页面或浏览器资料。") from None


def blank_template(source):
    return {"source": source, "account_id": "", "account_name": None,
            "captured_at": "", "source_data_at": None,
            "cash": {key: None for key in ("total_assets", "available", "frozen", "market_value", "cash_balance", "floating_pnl")},
            "positions": None, "orders": None, "executions": None, "scope": "user_supplied_official_capture"}
