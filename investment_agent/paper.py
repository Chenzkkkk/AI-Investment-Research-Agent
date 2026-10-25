"""SQLite-only simulated books, never linked to a securities account."""
from contextlib import contextmanager
from datetime import date
import json
import math
from pathlib import Path
import re
import sqlite3

from .backtest import BacktestConfig, fee, execution_block
from .common import DataError, now, symbol


class ResearchBook:
    def __init__(self, root: Path):
        self.path = Path(root) / "data/quant/research.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS accounts(name TEXT PRIMARY KEY, initial REAL, cash REAL, created TEXT);
            CREATE TABLE IF NOT EXISTS positions(account TEXT, symbol TEXT, quantity INTEGER, book_cost REAL, PRIMARY KEY(account,symbol));
            CREATE TABLE IF NOT EXISTS trades(id INTEGER PRIMARY KEY, account TEXT, symbol TEXT, day TEXT, side TEXT,
                quantity INTEGER, price REAL, fees REAL, realized REAL, created TEXT, evidence TEXT, request_id TEXT UNIQUE);
            CREATE TABLE IF NOT EXISTS predictions(id INTEGER PRIMARY KEY, symbol TEXT, created TEXT, entry_day TEXT,
                entry_price REAL, horizon INTEGER, probability REAL, thesis TEXT, dataset_id TEXT, evidence_hash TEXT,
                result TEXT);
            """)

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
        finally: db.close()

    def create(self, name: str, initial_cash=1_000_000) -> dict:
        if not re.fullmatch(r"[\w\-\u4e00-\u9fff]{1,32}", name) or not math.isfinite(initial_cash) or not 1000 <= initial_cash <= 1e9:
            raise DataError("PAPER_ACCOUNT", "组合名限32个文字/数字/下划线，初始现金1000至10亿元。")
        with self.db() as db:
            if db.execute("SELECT 1 FROM accounts WHERE name=?", (name,)).fetchone():
                raise DataError("PAPER_EXISTS", "该模拟组合已存在，未重置。")
            db.execute("INSERT INTO accounts VALUES(?,?,?,?)", (name, initial_cash, initial_cash, now()))
        return self.portfolio(name)

    def accounts(self):
        with self.db() as db: return [dict(r) for r in db.execute("SELECT * FROM accounts ORDER BY created")]

    def portfolio(self, name: str, marks: dict | None = None) -> dict:
        with self.db() as db:
            account = db.execute("SELECT * FROM accounts WHERE name=?", (name,)).fetchone()
            if not account: raise DataError("PAPER_MISSING", "本地模拟组合不存在。")
            positions = [dict(r) for r in db.execute("SELECT * FROM positions WHERE account=? AND quantity>0", (name,))]
            trades = [dict(r) for r in db.execute("SELECT * FROM trades WHERE account=? ORDER BY id", (name,))]
        equity, missing = account["cash"], []
        for p in positions:
            quote = (marks or {}).get(p["symbol"])
            if not quote:
                missing.append(p["symbol"])
                p.update({"market_value": None, "unrealized_pnl": None, "data_time": None})
            else:
                value = p["quantity"] * quote["price"]
                equity += value
                p.update({"market_value": value, "unrealized_pnl": value - p["book_cost"], "data_time": quote.get("date")})
        for p in positions: p["weight_pct"] = p["market_value"] / equity * 100 if not missing and equity else None
        return {"ok": True, "source": "local_paper_book", "data": {"account": dict(account), "positions": positions,
            "trades": trades[-200:], "equity": equity if not missing else None, "unpriced": missing,
            "realized_pnl": sum(t["realized"] for t in trades),
            "max_weight_pct": max((p["weight_pct"] or 0 for p in positions), default=0) if not missing else None,
            "warnings": (["持仓缺估值，未将缺价持仓当零计入权益。"] if missing else []) +
                        ["本地模拟组合，未连接真实证券账户；估值可能为历史收盘价。"]}}

    def trade(self, name, bar, manifest, side, quantity, request_id, config=None):
        """Explicit UI/CLI action only. Close-reference simulation, not an executable quote."""
        config = config or BacktestConfig()
        config.validate()
        code, day = symbol(bar["symbol"]), bar["date"]
        if manifest["synthetic"] or manifest["adjustment"] != "raw":
            raise DataError("PAPER_SOURCE", "正式模拟簿仅接受标明来源的不复权真实快照，测试请用隔离数据库。")
        if side not in {"buy", "sell"} or not isinstance(quantity, int) or quantity <= 0 or quantity % config.lot_size:
            raise DataError("PAPER_QUANTITY", "数量必须为正整数手数；方向为buy/sell。")
        if not re.fullmatch(r"[\w-]{8,80}", request_id): raise DataError("PAPER_ID", "需要唯一操作编号防止重复记账。")
        if date.fromisoformat(day) > date.today() or (date.today() - date.fromisoformat(day)).days > 7:
            raise DataError("PAPER_STALE", "模拟记账参考日线超过7个日历日，需更新行情。")
        from datetime import datetime
        clock = datetime.fromisoformat(now())
        if day == str(clock.date()) and clock.hour < 16:
            raise DataError("PAPER_OPEN_BAR", "当日日线在16时后用于收盘参考价模拟。")
        observed = {**bar, "open": bar["close"]}
        block = execution_block(observed, side, config.strict_limits)
        if block: raise DataError("PAPER_NOT_FILLABLE", block)
        price = bar["close"] * (1 + (config.slippage_bps + config.impact_bps)/10000 * (1 if side == "buy" else -1))
        limit = bar.get("limit_up" if side == "buy" else "limit_down")
        if limit is not None and math.isfinite(limit) and ((side == "buy" and price >= limit) or (side == "sell" and price <= limit)):
            raise DataError("PAPER_PRICE_LIMIT", "滑点后超出涨跌停参考价。")
        value = quantity * price
        cost = fee(value, side, config)
        with self.db() as db:
            existing = db.execute("SELECT * FROM trades WHERE request_id=?", (request_id,)).fetchone()
            if existing:
                if (existing["account"],existing["symbol"],existing["side"],existing["quantity"]) != (name,code,side,quantity):
                    raise DataError("PAPER_DUPLICATE_ID", "操作编号已用于其他成交。")
                return {"idempotent": True, "trade": dict(existing)}
            account = db.execute("SELECT * FROM accounts WHERE name=?", (name,)).fetchone()
            if not account: raise DataError("PAPER_MISSING", "先创建模拟组合。")
            last = db.execute("SELECT MAX(day) FROM trades WHERE account=?", (name,)).fetchone()[0]
            if last and day < last: raise DataError("PAPER_BACKDATE", "不能倒序补成交影响已发生的模拟状态。")
            held = db.execute("SELECT * FROM positions WHERE account=? AND symbol=?", (name,code)).fetchone()
            held_qty, book_cost = (held["quantity"], held["book_cost"]) if held else (0,0)
            bought_today = db.execute("SELECT COALESCE(SUM(quantity),0) FROM trades WHERE account=? AND symbol=? AND day=? AND side='buy'", (name,code,day)).fetchone()[0]
            realized = 0
            if side == "buy":
                cash = account["cash"] - value - cost
                if cash < -1e-8: raise DataError("PAPER_CASH", "模拟现金不足。")
                held_qty += quantity
                book_cost += value + cost
            else:
                if quantity > held_qty - bought_today: raise DataError("PAPER_T1", "超出可卖数量或当日买入尚未T+1解锁。")
                basis = book_cost * quantity / held_qty
                realized = value - cost - basis
                book_cost -= basis
                held_qty -= quantity
                cash = account["cash"] + value - cost
            db.execute("UPDATE accounts SET cash=? WHERE name=?", (cash,name))
            db.execute("INSERT OR REPLACE INTO positions VALUES(?,?,?,?)", (name,code,held_qty,book_cost))
            evidence = json.dumps({"dataset_id": manifest["id"], "sha256": manifest["sha256"], "source": manifest["source"],
                "basis": "历史日线收盘参考价+模拟滑点，非实时/真实成交", "config": config.__dict__}, ensure_ascii=False)
            cursor = db.execute("INSERT INTO trades(account,symbol,day,side,quantity,price,fees,realized,created,evidence,request_id) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (name,code,day,side,quantity,price,cost,realized,now(),evidence,request_id))
            trade_id = cursor.lastrowid
        return {"idempotent": False, "trade_id": trade_id, "price": price, "fees": cost, "basis": "仅本地模拟记账"}

    def predict(self, bar, manifest, horizon: int, probability: float, thesis: str) -> dict:
        from datetime import datetime
        if manifest["synthetic"] or manifest["adjustment"] != "raw": raise DataError("PREDICTION_SOURCE", "预测登记需真实不复权数据。")
        if not isinstance(horizon, int) or not 1 <= horizon <= 60 or not math.isfinite(probability) or not 0 <= probability <= 1 or not 1 <= len(thesis) <= 2000:
            raise DataError("PREDICTION_INPUT", "周期为1至60交易日，上涨概率0至1，需填写研究理由。")
        created = now()
        local = datetime.fromisoformat(created)
        if (local.date() - date.fromisoformat(bar["date"])).days > 7 or bar["date"] > str(local.date()):
            raise DataError("PREDICTION_STALE", "入场基准过旧或在未来，请更新数据。")
        if bar["date"] == str(local.date()) and local.hour < 16:
            raise DataError("PREDICTION_OPEN_BAR", "仅登记已收盘日线；当日日线在16时后使用。")
        with self.db() as db:
            cursor = db.execute("INSERT INTO predictions(symbol,created,entry_day,entry_price,horizon,probability,thesis,dataset_id,evidence_hash) VALUES(?,?,?,?,?,?,?,?,?)",
                (bar["symbol"],created,bar["date"],bar["close"],horizon,probability,thesis,manifest["id"],manifest["sha256"]))
            identity = cursor.lastrowid
        return {"ok": True, "source": "local_prediction_journal", "data": {"id": identity, "created": created,
            "basis": "此刻登记，评估只使用登记日期之后完整交易日日线；不允许回填预测时间。"}}

    def predictions(self):
        with self.db() as db: return [dict(r) for r in db.execute("SELECT * FROM predictions ORDER BY id DESC LIMIT 500")]

    def evaluate(self, frame, manifest):
        from datetime import datetime
        from .data_quality import sessions_for, quality_of
        if manifest["synthetic"] or manifest["adjustment"] != "raw": raise DataError("EVALUATION_SOURCE", "复盘需真实不复权数据。")
        evaluated, pending = [], []
        local = datetime.fromisoformat(now())
        for pred in self.predictions():
            if pred["result"]: continue
            created_day = pred["created"][:10]
            sourced=sessions_for(manifest)
            calendar = [d for d in sourced if max(created_day,pred["entry_day"])<d<=str(local.date())] if sourced is not None else sorted(frame.loc[(frame.date > max(created_day,pred["entry_day"])) & (frame.date <= str(local.date())),"date"].unique())
            if local.hour < 16: calendar=[d for d in calendar if d<str(local.date())]
            g = frame[(frame.symbol == pred["symbol"]) & frame.date.isin(calendar)].sort_values("date")
            if local.hour < 16: g = g[g.date < str(local.date())]
            required_dates=calendar[:pred["horizon"]]
            if len(calendar)<pred["horizon"] or not set(required_dates).issubset(set(g.date)):
                pending.append({"id": pred["id"], "available_bars": len(g), "required_bars": pred["horizon"]})
                continue
            target = g[g.date==required_dates[-1]].iloc[0]
            affected=[e for e in quality_of(manifest).get("corporate_actions",[]) if e["symbol"]==pred["symbol"] and pred["entry_day"]<e["ex_date"]<=target.date]
            if any(not e.get("accounting_supported") for e in affected):
                pending.append({"id":pred["id"],"reason":"预测区间含未计入的分红送转，暂不评分，避免把除权缺口当预测输赢。","events":affected})
                continue
            dividends=sum(e["cash_per_share_before_tax"] for e in affected if pred["entry_day"]<=e["record_date"]<=target.date)
            ret = float((target.close+dividends) / pred["entry_price"] - 1)
            outcome = int(ret > 0)
            result = {"evaluated_at": now(), "target_date": target.date, "exit_price": float(target.close),
                "return_pct": ret*100, "up": outcome, "direction_correct": bool((pred["probability"] >= .5) == bool(outcome)),
                "cash_dividends_per_share_before_tax":dividends,
                "brier_score": (pred["probability"] - outcome)**2, "dataset_id": manifest["id"], "sha256": manifest["sha256"],
                "limitations": "使用来源交易日历，缺中间/目标日线不评估；价格收益未含息" if sourced is not None else "旧快照无来源日历，仅按所给日线计数，目标日可能延后；请先补齐数据质量元信息"}
            with self.db() as db:
                cursor = db.execute("UPDATE predictions SET result=? WHERE id=? AND result IS NULL", (json.dumps(result,ensure_ascii=False),pred["id"]))
                if cursor.rowcount: evaluated.append({"id": pred["id"], **result})
        return {"ok": True, "source": "local_prediction_journal", "data": {"evaluated": evaluated, "pending": pending,
            "note": "已评估结果不覆盖；未成熟预测保留等待，不生成虚构命中率。"}}
