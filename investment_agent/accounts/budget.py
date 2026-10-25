from dataclasses import asdict, dataclass, field
import math
import re
from ..common import DataError


@dataclass
class BudgetPolicy:
    capital: float = 10000
    max_position: float = 0.8
    single_stock: float = 0.25
    cash_reserve: float = 0.2
    strategy_symbols: list[str] = field(default_factory=list)

    def __post_init__(self):
        if not math.isfinite(self.capital) or not 0 < self.capital <= 1e10:
            raise DataError("INVALID_BUDGET", "策略本金必须是有限正数。")
        if any(not math.isfinite(v) or not 0 <= v <= 1 for v in (self.max_position, self.single_stock, self.cash_reserve)):
            raise DataError("INVALID_BUDGET", "仓位、个股上限和现金保留比例必须在0到1之间。")


def budget_analysis(snapshot, policy, *, code=None, price=None, usable=False):
    """Conservative local arithmetic, never changes official cash or creates orders."""
    result = {"strategy_capital": policy.capital, "official_available": snapshot.cash.get("available") if snapshot else None,
              "official_total_assets": snapshot.cash.get("total_assets") if snapshot else None,
              "strategy_used": None, "strategy_remaining": None, "maximum_additional": None,
              "suggested_max_quantity": None, "reason": "尚无当前可用的账户快照。", "research_only": True}
    if not snapshot or snapshot.positions is None:
        return result
    positions = snapshot.positions
    if positions and not policy.strategy_symbols:
        result["reason"] = "官方持仓尚未明确归属策略；请指定计入本策略的持仓，不能假定全部由本策略产生。"
        return result
    selected = [row for row in positions if row.get("symbol") in policy.strategy_symbols]
    if any(row.get("market_value") is None for row in selected):
        result["reason"] = "策略持仓缺少来源市值；占用额度 unavailable。"
        return result
    used = sum(row["market_value"] for row in selected)
    result["known_holdings_used"] = used
    # Without a full order scope, commitments cannot safely be inferred as zero.
    if snapshot.orders is None:
        result["reason"] = "委托数据 unavailable，不能确认待成交买单占用。"
        return result
    commitments, stock_commitment = 0, 0
    for row in snapshot.orders:
        # Official GM enums: 3 filled, 5 canceled, 8 rejected, 12 expired.
        if snapshot.source != "DONGCAI_GM_SIM" or row.get("status") not in {1, 2, 3, 5, 8, 10, 12}:
            result["reason"] = "委托状态未识别，保守暂停新增仓位数量。"
            return result
        if row["status"] in {3, 5, 8, 12}:
            continue
        if row.get("side") == 2:
            continue
        if row.get("side") != 1 or row.get("symbol") not in policy.strategy_symbols or not row.get("order_id"):
            result["reason"] = "存在归属或方向未确认的待成交委托，无法计算策略占用。"
            return result
        if any(row.get(k) is None for k in ("quantity", "filled_quantity", "price")) or row["price"] <= 0:
            result["reason"] = "待成交买单缺少可约束的数量或价格。"
            return result
        commitment = (row["quantity"]-row["filled_quantity"])*row["price"]*1.003
        commitments += commitment
        if row["symbol"] == code:
            stock_commitment += commitment
    result.update(strategy_used=used+commitments, strategy_remaining=max(0,policy.capital-used-commitments),
                  pending_buy_commitment=commitments)
    if not usable:
        result["reason"] = "账户快照过期、未核验或仅手工导入，不生成可执行数量。"
        return result
    available = snapshot.cash.get("available")
    if available is None:
        result["reason"] = "官方可用资金 unavailable。"
        return result
    held_in_stock = sum(row["market_value"] for row in selected if row.get("symbol") == code)
    allowed = max(0, min(available, policy.capital * min(policy.max_position, 1-policy.cash_reserve) - used-commitments,
                         policy.capital * policy.single_stock - held_in_stock-stock_commitment))
    result.update(maximum_additional=round(allowed, 2), reason="基于已核验快照的研究上限；交易前仍需本人核对官方可买量、费用和交易规则。")
    if price is not None:
        if not isinstance(price, (int, float)) or isinstance(price, bool) or not math.isfinite(price) or price <= 0:
            raise DataError("INVALID_PRICE", "参考价格必须是有限正数。")
        if code and re.fullmatch(r"(?:60[0135]\d{3}\.SH|00[0123]\d{3}\.SZ|30[01]\d{3}\.SZ)", code):
            # Fee cushion only bounds research sizing; not an exchange fee/matching calculation.
            result["suggested_max_quantity"] = int(allowed / (price * 1.003) // 100 * 100)
            result["sizing_basis"] = "仅普通沪深A股买入整百股及0.3%研究缓冲；未支持的市场/品种数量保持unavailable。"
    return result
