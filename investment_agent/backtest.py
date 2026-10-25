"""Small deterministic daily, long-only simulator. No broker or SDK dependency."""
from dataclasses import dataclass, asdict, replace
import hashlib
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from .common import DataError, now
from .factors import factor_table


@dataclass(frozen=True)
class BacktestConfig:
    initial_cash: float = 1_000_000
    lookback: int = 20
    rebalance_days: int = 5
    top_n: int = 2
    max_weight: float = 0.5
    commission_bps: float = 3
    min_commission: float = 5
    sell_tax_bps: float = 5
    slippage_bps: float = 5
    impact_bps: float = 2
    lot_size: int = 100
    max_participation: float = 0.01
    signal_delay: int = 1
    strict_limits: bool = True
    holdout_fraction: float = 0.3
    corporate_action_policy: str = "cash_dividends"

    def validate(self):
        if self.corporate_action_policy not in {"block","cash_dividends","price_only"}:
            raise DataError("CORPORATE_ACTION_POLICY","公司行动策略仅block、cash_dividends或price_only。")
        vals = asdict(self)
        if any(isinstance(v, (int, float)) and not math.isfinite(v) for v in vals.values()):
            raise DataError("BACKTEST_CONFIG", "参数必须为有限数字。")
        if not (1000 <= self.initial_cash <= 1e9 and 5 <= self.lookback <= 120 and 1 <= self.top_n <= 5
                and 1 <= self.rebalance_days <= 60 and 0 < self.max_weight <= 1 and self.lot_size >= 1
                and 1 <= self.signal_delay <= 5 and 0 < self.max_participation <= .1 and .1 <= self.holdout_fraction <= .5):
            raise DataError("BACKTEST_CONFIG", "回测参数越界。")
        if any(getattr(self, x) < 0 or getattr(self, x) > 1000 for x in
               ["commission_bps", "min_commission", "sell_tax_bps", "slippage_bps", "impact_bps"]):
            raise DataError("BACKTEST_COST", "成本参数需0至1000。")


def fee(notional: float, side: str, config: BacktestConfig) -> float:
    return max(config.min_commission, notional * config.commission_bps / 10000) + (notional * config.sell_tax_bps / 10000 if side == "sell" else 0)


def execution_block(bar: dict, side: str, strict: bool) -> str | None:
    if not bar or not bool(bar.get("tradable", True)) or float(bar.get("volume", 0)) <= 0: return "停牌/无日线/零成交量"
    flag = bar.get("can_" + side)
    if flag is not None and not pd.isna(flag):
        if not bool(flag): return "来源标记开盘不可" + side
        return None
    key = "limit_up" if side == "buy" else "limit_down"
    limit = bar.get(key)
    if limit is not None and pd.notna(limit):
        if (side == "buy" and bar["open"] >= limit - 1e-8) or (side == "sell" and bar["open"] <= limit + 1e-8):
            return "开盘触及涨跌停，保守拒绝"
    elif strict:
        return "缺开盘可成交/涨跌停字段，严格模式拒绝"
    elif bar["high"] == bar["low"]:
        return "一字价格日且涨跌停未知，保守拒绝"
    return None


def metrics(values: list[float]) -> dict:
    a = np.asarray(values, dtype=float)
    if len(a) < 2: return {"observations": len(a)}
    r = a[1:] / a[:-1] - 1
    vol = r.std(ddof=1) if len(r) > 1 else 0
    dd = 1 - a / np.maximum.accumulate(a)
    annual = float((a[-1] / a[0]) ** (252 / len(r)) - 1)
    downside = np.sqrt(np.mean(np.minimum(r, 0) ** 2))
    return {"observations": len(r), "total_return_pct": float((a[-1]/a[0]-1)*100),
        "annual_return_pct": annual * 100, "annual_volatility_pct": float(vol * np.sqrt(252) * 100),
        "sharpe": float(r.mean() / vol * np.sqrt(252)) if vol > 1e-12 else None,
        "sortino": float(r.mean() / downside * np.sqrt(252)) if downside > 1e-12 else None,
        "calmar": float(annual / dd.max()) if dd.max() > 1e-12 else None,
        "max_drawdown_pct": float(dd.max() * 100), "daily_positive_pct": float((r > 0).mean() * 100),
        "var95_daily_pct": float(np.quantile(r, .05) * 100),
        "basis": "日频/252年化/无风险率0，日胜率不是交易胜率；短样本年化不稳定"}


def simulate(frame: pd.DataFrame, config: BacktestConfig, trade_start="", calendar=None,events=()) -> dict:
    config.validate()
    days = sorted(d for d in calendar if frame.date.min()<=d<=frame.date.max()) if calendar is not None else sorted(frame.date.unique())
    if len(days) < config.lookback + config.signal_delay + 4:
        raise DataError("SHORT_BACKTEST", "样本过短，至少需回看窗口+延迟+4个交易日。")
    stocks = sorted(frame.symbol.unique())
    lookup = {(r["date"], r["symbol"]): r for r in frame.to_dict("records")}
    cash = config.initial_cash
    positions = {c: 0 for c in stocks}
    last_buy = {}
    marks = {}
    curve, trades, rejected, signals = [], [], [], []
    baseline_units = None
    cumulative_costs = 0
    entitlements,receivables,dividend_flows={},{},[]
    benchmark_cash=0.
    first_index = max(config.lookback + config.signal_delay, next((i for i,d in enumerate(days) if d >= trade_start), 0))
    previous_day = days[first_index - 1]
    curve.append({"date": previous_day, "equity": cash, "gross_reference":cash,"benchmark": cash, "cash": cash, "exposure": 0, "max_weight": 0, "stale_symbols": []})
    for i in range(first_index, len(days)):
        day = days[i]
        bars = {c: lookup.get((day, c)) for c in stocks}
        for c, bar in bars.items():
            if bar: marks[c] = bar["open"]
        # Existing holdings with missing marks keep last known value, never disappear.
        if baseline_units is None:
            if any(b is None for b in bars.values()):
                raise DataError("BENCHMARK_GAP", "基准首个成交日有股票缺开盘价，无法按完整固定池建立基准。")
            baseline_units = {c: config.initial_cash / len(stocks) / b["open"] for c,b in bars.items()}
        for key,event in enumerate(events):
            if event["ex_date"]==day:
                if event["record_date"]>=days[first_index] and key not in entitlements:
                    raise DataError("DIVIDEND_ENTITLEMENT","缺股权登记日持仓记录，不能推定分红资格。")
                held,bmheld=entitlements.get(key,(0,0))
                receivables[key]={"amount":held*event["cash_per_share_before_tax"],"benchmark_amount":bmheld*event["cash_per_share_before_tax"],"pay_date":event["pay_date"]}
            due=receivables.get(key)
            if due and due["pay_date"]<=day:
                cash+=due["amount"]
                benchmark_cash+=due["benchmark_amount"]
                dividend_flows.append({"date":day,"symbol":event["symbol"],"record_date":event["record_date"],"ex_date":event["ex_date"],
                    "cash_cny":due["amount"],"benchmark_cash_cny":due["benchmark_amount"],"basis":"税前纯现金分红，个人持有期税未模拟"})
                del receivables[key]
        pending_cash=sum(r["amount"] for r in receivables.values())
        if (i - first_index) % config.rebalance_days == 0:
            signal_day = days[i - config.signal_delay]
            history = frame[frame.date <= signal_day]
            ranks = factor_table(history, signal_day, config.lookback)["ranking"]
            # Exclude stale records, but do not inspect today's returns when selecting.
            selected = [r["symbol"] for r in ranks[:config.top_n]]
            weight = min(config.max_weight, 1 / len(selected)) if selected else 0
            equity_open = cash + pending_cash + sum(q * marks.get(c, 0) for c,q in positions.items())
            targets = {}
            for c in stocks:
                # Size using information known at signal time; opening gap is handled by cash check.
                prior = lookup.get((signal_day, c))
                target = int(equity_open * weight / prior["close"] / config.lot_size) * config.lot_size if c in selected and prior else 0
                targets[c] = target
            signals.append({"signal_date": signal_day, "execution_date": day, "selected": selected, "target_weight": weight})
            for side in ["sell", "buy"]:
                for c in stocks:
                    difference = targets[c] - positions[c]
                    qty = (difference if side == "buy" else -difference)
                    if qty <= 0: continue
                    requested_qty = qty
                    bar = bars[c]
                    reason = execution_block(bar, side, config.strict_limits)
                    if side == "sell" and last_buy.get(c) == day: reason = "T+1持仓未解锁"
                    if reason:
                        rejected.append({"date": day, "symbol": c, "side": side, "quantity": qty, "reason": reason})
                        continue
                    prior_volume = history[history.symbol == c].tail(20).volume.mean()
                    cap = int(prior_volume * config.max_participation / config.lot_size) * config.lot_size
                    qty = min(qty, cap)
                    if side == "sell": qty = min(qty, positions[c])
                    if qty <= 0:
                        rejected.append({"date": day, "symbol": c, "side": side, "reason": "过去成交量容量不足"})
                        continue
                    impact = (config.slippage_bps + config.impact_bps) / 10000
                    price = bar["open"] * (1 + impact if side == "buy" else 1 - impact)
                    # Price limit applies after modeled slippage too.
                    lim = bar.get("limit_up" if side == "buy" else "limit_down")
                    if lim is not None and pd.notna(lim) and ((side == "buy" and price >= lim) or (side == "sell" and price <= lim)):
                        rejected.append({"date": day, "symbol": c, "side": side, "reason": "滑点后越过涨跌停"})
                        continue
                    if side == "buy":
                        qty = min(qty, int(max(0, cash - config.min_commission) / (price * (1 + config.commission_bps / 10000)) / config.lot_size) * config.lot_size)
                    if qty <= 0:
                        rejected.append({"date": day, "symbol": c, "side": side, "reason": "现金不足一手"})
                        continue
                    if qty < requested_qty:
                        rejected.append({"date": day, "symbol": c, "side": side, "quantity": requested_qty-qty,
                                         "reason": "容量/现金限制，仅部分模拟成交；余量取消"})
                    notional = qty * price
                    cost = fee(notional, side, config)
                    cash += notional - cost if side == "sell" else -notional - cost
                    positions[c] += qty if side == "buy" else -qty
                    if side == "buy": last_buy[c] = day
                    trades.append({"date": day, "signal_date": signal_day, "symbol": c, "side": side,
                        "quantity": qty, "price": price, "open": bar["open"], "notional": notional, "fees": cost,
                        "requested_quantity": requested_qty,
                        "slippage_impact_cny": abs(price - bar["open"]) * qty, "prior_adv_shares": prior_volume})
                    cumulative_costs += cost + abs(price - bar["open"]) * qty
        for c,b in bars.items():
            if b: marks[c] = b["close"]
        for key,event in enumerate(events):
            if event["record_date"]==day:
                entitlements[key]=(positions.get(event["symbol"],0),baseline_units.get(event["symbol"],0))
        value = sum(q * marks.get(c, 0) for c,q in positions.items())
        equity = cash + pending_cash + value
        if cash < -1e-5 or equity <= 0: raise DataError("ACCOUNT_INVARIANT", "模拟账户现金/权益不合法。")
        curve.append({"date": day, "equity": equity, "gross_reference":equity+cumulative_costs, "cash": cash,
            "receivables_cny":pending_cash,
            "benchmark": benchmark_cash+sum(r["benchmark_amount"] for r in receivables.values())+sum(q * marks.get(c, 0) for c,q in baseline_units.items()),
            "exposure": value/equity, "max_weight": max((q*marks.get(c,0)/equity for c,q in positions.items()), default=0),
            "stale_symbols": [c for c,q in positions.items() if q and not bars[c]]})
    stats = metrics([r["equity"] for r in curve])
    bm = metrics([r["benchmark"] for r in curve])
    stats.update({"turnover_one_way": sum(t["notional"] for t in trades) / (2 * np.mean([r["equity"] for r in curve])),
        "gross_same_fills_return_pct": (curve[-1]["gross_reference"]/config.initial_cash-1)*100,
        "fees_cny": sum(t["fees"] for t in trades), "slippage_impact_cny": sum(t["slippage_impact_cny"] for t in trades),
        "trades": len(trades), "rejected_orders": len(rejected),
        "cash_dividends_cny":sum(r["cash_cny"] for r in dividend_flows),"ending_dividend_receivables_cny":sum(r["amount"] for r in receivables.values()),
        "mean_exposure_pct": float(np.mean([r["exposure"] for r in curve]) * 100),
        "max_single_weight_pct": max(r["max_weight"] for r in curve) * 100,
        "excess_return_pp": stats["total_return_pct"] - bm["total_return_pct"]})
    return {"metrics": stats, "benchmark_metrics": bm, "curve": curve, "trades": trades,
            "rejected": rejected, "signals": signals, "final_positions": positions,"dividend_flows":dividend_flows}


def run_backtest(frame, manifest, config: BacktestConfig, root: Path, sensitivity=True) -> dict:
    from .data_quality import guard_corporate_actions, sessions_for
    quality=guard_corporate_actions(manifest,config)
    calendar=sessions_for(manifest)
    events=quality.get("corporate_actions",[]) if config.corporate_action_policy=="cash_dividends" else []
    if manifest["adjustment"] != "raw":
        raise DataError("ADJUSTED_EXECUTION", "成交模拟只接受不复权价格；复权价可用于因子观察。")
    result = simulate(frame, config,calendar=calendar,events=events)
    days = sorted(d for d in calendar if manifest["start"]<=d<=manifest["end"]) if calendar is not None else sorted(frame.date.unique())
    trade_days = days[config.lookback + config.signal_delay:]
    split = trade_days[int(len(trade_days) * (1-config.holdout_fraction))]
    holdout = simulate(frame, config, trade_start=split,calendar=calendar,events=events)
    samples = []
    variants = [("双倍成本", replace(config, commission_bps=config.commission_bps*2, min_commission=config.min_commission*2,
                                  sell_tax_bps=config.sell_tax_bps*2, slippage_bps=config.slippage_bps*2, impact_bps=config.impact_bps*2)),
                ("延迟多一日", replace(config, signal_delay=min(config.signal_delay+1, 5))),
                ("回看增加5日", replace(config, lookback=config.lookback+5))]
    if sensitivity:
        for label, variant in variants:
            try: samples.append({"variant": label, "config": asdict(variant), "metrics": simulate(frame, variant,calendar=calendar,events=events)["metrics"]})
            except DataError as e: samples.append({"variant": label, "unavailable": str(e)})
    # Fixed block resampling, descriptive interval, not a significance claim.
    returns = pd.Series([r["equity"] for r in result["curve"]]).pct_change().dropna().to_numpy()
    rng = np.random.default_rng(42)
    draws = []
    for _ in range(300):
        sampled = np.concatenate([returns[j:j+5] for j in rng.integers(0, max(1, len(returns)-4), size=math.ceil(len(returns)/5))])[:len(returns)]
        draws.append(float(np.prod(1 + sampled)-1)*100)
    report = {"ok": True, "source": "python_quant", "data_time": manifest["end"], "created_at": now(),
        "dataset_id": manifest["id"], "dataset_sha256": manifest["sha256"], "synthetic": manifest["synthetic"],
        "data_quality":quality or {"status":"未补充来源交易日历/分红元数据；使用已提供日线日期，未知不能当不存在"},
        "return_basis":"计入已获取事件表的税前纯现金分红；含应收分红，个人红利税未模拟" if events else "价格收益；没有完整公司行动含息保证",
        "config": asdict(config), "decision": "exploratory / 探索性模拟" if result["trades"] else "no_fills / 无成交，策略有效性不可评估", "data": result,
        "holdout": {"split_date": split, "mode": "预设参数，时间顺序留出；独立现金重启，复用历史预热，不训练/调参", "metrics": holdout["metrics"], "benchmark_metrics": holdout["benchmark_metrics"]},
        "sensitivity": samples, "trials": 1+len(samples), "bootstrap": {"seed": 42, "draws": 300, "block_days": 5,
            "return_interval_95_pct": np.quantile(draws, [.025,.975]).tolist(), "interpretation": "固定样本日收益块重采样，仅描述，不保证未来收益或显著性"},
        "assumptions": ["前一已收盘交易日形成信号，下一个可用交易日开盘模拟；延迟可调。",
            "只做多、无借贷；手数由参数决定；买入份额当日不可卖，卖出先于买入。",
            "成本为用户可调研究假设，不代表实际券商费率/历年税制；冲击为固定bp。",
            "容量使用信号日前20根日线均量，不使用成交日全天量预知规模。",
            "日线无法还原盘口、排队或真实部分成交；未成交单当次取消，不隐式顺延。",
            "单股权重上限作用于调仓目标，价格漂移可能超过，风险中报告实际最大值。",
            "基准为同一固定池从首个模拟交易日开盘等权买入持有的毛价格收益，允许碎股、不含费；不是指数超额或alpha。",
            "有增强事件表时，现金模式按登记日持仓/除息应收/派息到账处理税前纯现金分红；否则仅价格收益。送转/配股缺模型则阻断，退市清算/历史全市场池未覆盖。",
            "缺失日线保留旧估值并列出stale_symbols，不伪造成交；不具备正式生产回测认证。",
            "严格模式缺开盘可交易标记/涨跌停价拒绝成交；宽松模式缺字段只作乐观探索。",
            "样本外切点固定，查看后再调参数就不再是未触碰测试集；未做大规模寻优或显著性校正。"]}
    source_bytes = b"".join((Path(__file__).parent / name).read_bytes() for name in ["backtest.py", "factors.py", "quant_data.py","data_quality.py"])
    report["engine_sha256"] = hashlib.sha256(source_bytes).hexdigest()
    out = Path(root) / "data/quant/backtests"
    out.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(json.dumps([manifest["id"], asdict(config), report["engine_sha256"], sensitivity], sort_keys=True).encode()).hexdigest()[:20]
    path = out / (digest + ".json")
    report["report_file"] = str(path)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    return report
