from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation
import json
import re
from typing import Any

TZ = timezone(timedelta(hours=8))


def now() -> str:
    return datetime.now(TZ).isoformat(timespec="seconds")


class DataError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def symbol(value: str) -> str:
    v = value.strip().upper()
    if re.fullmatch(r"\d{6}", v):
        suffix = "BJ" if v.startswith(("4", "8", "92")) else "SH" if v.startswith(("6", "5", "9")) else "SZ"
        v += "." + suffix
    if not re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", v):
        raise DataError("INVALID_SYMBOL", "请输入六位 A 股代码，例如 600519.SH。")
    if v.startswith("92") and v.endswith(".SH"):
        v = v[:6] + ".BJ"
    return v


def safe(value: Any, secrets: tuple[str, ...] = ()) -> Any:
    if isinstance(value, dict):
        return {str(k): safe(v, secrets) for k, v in value.items()
                if not re.search(r"api.?key|password|userinfo|authorization|access.?token", str(k), re.I)}
    if isinstance(value, (list, tuple)):
        return [safe(x, secrets) for x in value]
    if isinstance(value, str):
        for secret in secrets:
            if secret:
                value = value.replace(secret, "[REDACTED]")
        return re.sub(r"\b(?:em_|sk-)[A-Za-z0-9_.-]{8,}", "[REDACTED]", value)
    return value


def number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        v = str(value).strip().replace(",", "")
        # Vendor screen CSVs encode cells as "12.24|2023年报". Preserve period in raw row.
        if re.fullmatch(r"[-+]?\d+(?:\.\d+)?%?\|\d{4}年(?:报|一季报|中报|三季报)", v):
            v = v.split("|", 1)[0]
        multiplier = 1
        for unit, scale in (("万亿", 1e12), ("亿", 1e8), ("万", 1e4)):
            if unit in v:
                v = v.replace(unit, "")
                multiplier = scale
                break
        v = re.sub(r"(?:元|股|倍|%)$", "", v)
        d = Decimal(v) * Decimal(str(multiplier))
        return float(d) if d.is_finite() else None
    except (InvalidOperation, ValueError):
        return None


@dataclass
class Result:
    source: str
    data: Any = None
    symbol: str | None = None
    kind: str = "research"
    data_time: str | None = None
    time_basis: str = "服务返回值；未提供则未知"
    fetched_at: str = field(default_factory=now)
    ok: bool = True
    error: dict | None = None
    cached: bool = False
    metadata: dict = field(default_factory=dict)

    def dict(self) -> dict:
        return asdict(self)


def failed(source: str, exc: Exception) -> Result:
    return Result(source=source, ok=False, error={"code": getattr(exc, "code", "UNAVAILABLE"),
                  "message": str(exc) if isinstance(exc, DataError) else "数据源不可用：" + type(exc).__name__})


def unpack_mcp(content: list) -> Any:
    result = []
    for block in content:
        if block.get("type") != "text":
            continue
        value = block.get("text", "")
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            pass
        result.append(value)
    return result[0] if len(result) == 1 else result


def quote_from_mcp(data: Any, code: str) -> dict | None:
    if not isinstance(data, dict):
        return None
    quotes = []
    for table in data.get("data", []):
        columns = table.get("columns", [])
        if not columns or code[:6] not in str(columns[0]):
            continue
        for row in table.get("items", []):
            if len(row) >= 2 and row[0] in {"最新价", "收盘价"}:
                price = number(row[1])
                if price is not None and price > 0:
                    quotes.append({"price": price, "currency": "CNY", "unit": "元/股",
                                   "price_basis": row[0], "data_time": str(columns[1]) if len(columns) > 1 else None})
    return max(quotes, key=lambda q: (q["price_basis"] == "最新价", q["data_time"] or "")) if quotes else None
