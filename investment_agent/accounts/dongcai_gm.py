"""Reader for an already-running, explicitly bound GM simulation context.

No import *, run(), token handling, subscription, order, cancel, or transfer methods.
MODE_LIVE means realtime, NOT proof of a simulation account.
"""
from .models import Snapshot, utc_now
from ..common import DataError, symbol


class GMReadOnly:
    __slots__ = ("cash", "positions", "orders", "unfinished", "executions")

    def __init__(self, api):
        self.cash = api.get_cash
        self.positions = api.get_position
        self.orders = api.get_orders
        self.unfinished = api.get_unfinished_orders
        self.executions = api.get_execution_reports


def iso(value):
    return value.isoformat() if hasattr(value, "isoformat") else value or None


def stock_code(value):
    parts = str(value).split(".")
    if len(parts) != 2 or parts[0] not in {"SHSE", "SZSE", "BJSE"}:
        raise DataError("GM_ASSET_UNSUPPORTED", "当前适配器只读取沪深京股票，其他品种不套用股票字段。")
    return symbol(parts[1]+"."+{"SHSE":"SH","SZSE":"SZ","BJSE":"BJ"}[parts[0]])


class DongcaiGMProvider:
    source = "DONGCAI_GM_SIM"

    def __init__(self, api=None, *, terminal_binding=None):
        self._binding = terminal_binding or {}
        self._api = GMReadOnly(api) if api is not None else None
        self.live_ready = bool(self._api and self._binding.get("simulation_confirmed_by_user")
                               and self._binding.get("terminal_account_id") and self._binding.get("strategy_id"))

    def fetch(self, target):
        binding = self._binding
        if not self.live_ready or binding.get("terminal_account_id") != target.account_id:
            raise DataError("GM_CONTEXT_REQUIRED", "需要官方终端已关联的仿真账户、策略上下文及本人确认的唯一ID；未自动启动GM或将MODE_LIVE当作仿真证明。")
        api = self._api
        account = target.account_id
        first = api.cash(account_id=account)
        if not isinstance(first, dict) or not first:
            raise DataError("ACCOUNT_NOT_FOUND", "指定GM账户未返回资金，未改查默认账户。")
        if first.get("account_id") != account:
            raise DataError("ACCOUNT_IDENTITY_MISMATCH", "GM返回另一个账户，拒绝同步。")
        position_rows = api.positions(account_id=account)
        all_orders, all_unfinished, all_executions = api.orders(), api.unfinished(), api.executions()
        last = api.cash(account_id=account)
        if not isinstance(last, dict) or last.get("account_id") != account:
            raise DataError("ACCOUNT_IDENTITY_MISMATCH", "GM账户在读取期间变化。")
        # Block torn balance/position snapshots. Market-driven NAV movement alone is not a fill.
        if any(first.get(k) != last.get(k) for k in ("available", "order_frozen", "balance")):
            raise DataError("ACCOUNT_READ_CHANGED", "读取期间资金变化，保留旧快照；请稍后重新同步，不推断成交。")

        warnings = ["分项查询非原子快照；GM委托及成交查询为日内范围，首次接入不代表完整历史。",
                    "官方终端手动交易是否完整进入此策略的委托/执行回报查询，仍须本人手动交易后的同账户端到端核验。"]

        def scoped(rows):
            if not isinstance(rows, list):
                raise DataError("GM_RESPONSE_SCHEMA", "GM查询未返回预期明细列表。")
            if any(not isinstance(row, dict) or not row.get("account_id") for row in rows):
                raise DataError("GM_RESPONSE_SCHEMA", "GM明细缺少账户ID，拒绝以默认账户猜测归属。")
            return [row for row in rows if row["account_id"] == account]

        def pos(row):
            return {"account_id": account, "symbol": stock_code(row.get("symbol")), "name": None,
                    "quantity": row.get("volume"), "sellable": row.get("available_now"),
                    "cost_price": row.get("vwap"), "cost_amount": row.get("amount"),
                    "price": row.get("price"), "market_value": row.get("market_value"),
                    "floating_pnl": row.get("fpnl"), "updated_at": iso(row.get("updated_at"))}

        def order(row):
            return {"account_id": account, "order_id": row.get("cl_ord_id"), "symbol": stock_code(row.get("symbol")),
                    "side": row.get("side"), "status": row.get("status"), "quantity": row.get("volume"),
                    "filled_quantity": row.get("filled_volume"), "price": row.get("price"),
                    "created_at": iso(row.get("created_at")), "updated_at": iso(row.get("updated_at"))}

        def execution(row):
            return {"account_id": account, "execution_id": row.get("exec_id"), "order_id": row.get("cl_ord_id"),
                    "symbol": stock_code(row.get("symbol")), "side": row.get("side"), "quantity": row.get("volume"),
                    "price": row.get("price"), "amount": row.get("amount"), "fees": row.get("commission"),
                    "execution_type": row.get("exec_type"),
                    "executed_at": iso(row.get("created_at"))}

        mapped_orders = [order(r) for r in scoped(all_orders)]
        # Only use unfinished orders to check consistency, never overwrite a newer full order row.
        known = {r["order_id"] for r in mapped_orders if r["order_id"]}
        if any(r.get("cl_ord_id") not in known for r in scoped(all_unfinished)):
            raise DataError("ACCOUNT_READ_CHANGED", "委托列表与未结委托不同步；本次不替换快照。")
        return Snapshot(source=self.source, account_id=account, account_name=last.get("account_name") or None,
                        captured_at=utc_now(), source_data_at=iso(last.get("updated_at")),
                        cash={"total_assets": last.get("nav"), "available": last.get("available"),
                              "frozen": last.get("order_frozen"), "market_value": last.get("market_value"),
                              "cash_balance": last.get("balance"), "floating_pnl": last.get("fpnl"),
                              "total_pnl": last.get("pnl")},
                        positions=[pos(r) for r in scoped(position_rows)], orders=mapped_orders,
                        executions=[execution(r) for r in scoped(all_executions)], transport="official_sdk",
                        scope="sdk_context_intraday_manual_trade_coverage_unverified", warnings=warnings)
