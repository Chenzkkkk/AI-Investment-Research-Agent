from datetime import datetime, timezone
import math
from .models import timestamp, numeric
from ..common import DataError


def close(a, b, tolerance=0.01):
    return a is not None and b is not None and math.isclose(float(a), float(b), rel_tol=0, abs_tol=tolerance)


def consistency(snapshot):
    cash = snapshot.cash
    if all(cash.get(k) is not None for k in ("total_assets", "cash_balance", "market_value")):
        if not close(cash["total_assets"], cash["cash_balance"] + cash["market_value"], 1.0):
            raise DataError("ACCOUNT_INCONSISTENT", "总资产与现金余额加证券市值不一致，未自动改写官方数值。")
    if snapshot.positions is not None:
        symbols = [row.get("symbol") for row in snapshot.positions]
        if any(not s for s in symbols) or len(set(symbols)) != len(symbols):
            raise DataError("ACCOUNT_INCONSISTENT", "持仓代码缺失或重复，不自动加总。")
        if all(row.get("market_value") is not None for row in snapshot.positions) and cash.get("market_value") is not None:
            if not close(sum(row["market_value"] for row in snapshot.positions), cash["market_value"], 1.0):
                raise DataError("ACCOUNT_INCONSISTENT", "持仓市值合计与资金快照不一致，保留旧快照。")


def compare_official(snapshot, evidence):
    """Human reads official UI and enters values; name alone can never verify identity."""
    failures = []
    if not isinstance(evidence,dict) or not isinstance(evidence.get("cash"),dict):
        raise DataError("INVALID_REVIEW", "核对记录须包含官方资金对象。")
    if evidence.get("account_id") != snapshot.account_id or evidence.get("source") != snapshot.source:
        failures.append("unique_account_id_or_source")
    observed = timestamp(evidence.get("observed_at"))
    if abs((observed - timestamp(snapshot.captured_at)).total_seconds()) > 120:
        failures.append("observation_time_difference_over_120s")
    if abs((datetime.now(timezone.utc) - observed).total_seconds()) > 300:
        failures.append("official_observation_not_recent")
    official_cash = evidence.get("cash", {})
    for key in ("total_assets", "available", "market_value"):
        if not close(snapshot.cash.get(key), numeric(official_cash.get(key))):
            failures.append("cash." + key)
    expected, actual = evidence.get("positions"), snapshot.positions
    if not isinstance(expected, list) or actual is None:
        failures.append("positions_unavailable")
    else:
        by_code = {row.get("symbol"): row for row in expected if isinstance(row, dict)}
        if len(by_code) != len(expected) or set(by_code) != {row.get("symbol") for row in actual}:
            failures.append("position_symbols")
        for row in actual:
            observed_row = by_code.get(row.get("symbol"), {})
            for key in ("quantity", "sellable", "cost_price", "market_value"):
                if not close(row.get(key), numeric(observed_row.get(key))):
                    failures.append("position." + str(row.get("symbol")) + "." + key)
    return sorted(set(failures))
