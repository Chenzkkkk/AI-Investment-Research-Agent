"""Bounded phase-two tool facade shared by DeepSeek, CLI and Streamlit."""
from datetime import date
import hashlib
import json
from pathlib import Path

from .backtest import BacktestConfig, run_backtest
from .common import DataError, now
from .factors import factor_table
from .paper import ResearchBook
from .quant_data import DatasetStore, collect_history


class QuantLab:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.store = DatasetStore(root)
        self.book = ResearchBook(root)

    @staticmethod
    def marks(frame):
        return {code: {"price": float(g.iloc[-1].close), "date": g.iloc[-1].date}
                for code,g in frame.sort_values("date").groupby("symbol")}

    def catalog(self):
        skills = []
        for p in (self.root / "research-skills").glob("*/SKILL.md"):
            skills.append({"name": p.parent.name, "path": str(p), "mode": "方法论说明；不自动执行下载脚本"})
        return {"ok": True, "source": "local_quant_catalog", "data": {"datasets": self.store.list(),
            "paper_accounts": self.book.accounts(), "skills": skills}}

    def analyze(self, dataset_id: str, as_of="", lookback=20):
        frame, meta = self.store.load(dataset_id)
        if as_of and (as_of > meta["end"] or as_of < meta["start"]): raise DataError("AS_OF", "分析日期须在数据覆盖范围内。")
        return {"ok": True, "source": "python_factors", "data_time": as_of or meta["end"],
            "dataset_id": dataset_id, "synthetic": meta["synthetic"], "data": factor_table(frame,as_of,lookback),
            "data_quality":meta.get("provenance",{}).get("quality",{})}

    def backtest(self, dataset_id, config=None):
        frame, meta = self.store.load(dataset_id)
        return run_backtest(frame,meta,BacktestConfig(**(config or {})),self.root)

    def portfolio(self, name, dataset_id=""):
        marks = None
        if dataset_id:
            frame,meta = self.store.load(dataset_id)
            if meta["synthetic"]: raise DataError("PAPER_SYNTHETIC", "不能用合成行情为正式模拟簿估值。")
            marks = self.marks(frame)
        result = self.book.portfolio(name,marks)
        result["data"]["dataset_id"] = dataset_id or None
        info=result["data"]
        if dataset_id:
            held={p["symbol"] for p in info["positions"]}
            actions=[e for e in meta.get("provenance",{}).get("quality",{}).get("corporate_actions",[]) if e["symbol"] in held]
            if actions:
                info["warnings"].append("持仓涉及快照中的公司行动；模拟簿尚未自动入账分红/送转，所示账簿盈亏不含这些资金或股份。回测现金分红模型与模拟簿分开记账。")
                info["corporate_actions_to_reconcile"]=actions
        if marks and info["equity"] and not info["unpriced"]:
            import numpy as np
            import pandas as pd
            weights={p["symbol"]:p["market_value"]/info["equity"] for p in info["positions"]}
            sectors={}
            for code,g in frame.sort_values("date").groupby("symbol"):
                if code in weights:
                    sector=str(g.iloc[-1].get("sector","未分类"))
                    sectors[sector]=sectors.get(sector,0)+weights[code]*100
            risk={"cash_weight_pct":info["account"]["cash"]/info["equity"]*100,"sector_weights_pct":sectors,
                "parallel_price_shock_pnl_cny":{f"{int(s*100)}%":sum(p["market_value"] for p in info["positions"])*s for s in [-.1,-.2]},
                "marks":marks,"basis":"按当前模拟权重重放近期历史风险，不是模拟簿真实历史收益；平行冲击为假设场景。"}
            if weights:
                prices=frame.pivot(index="date",columns="symbol",values="close").reindex(columns=list(weights))
                daily=prices.pct_change(fill_method=None).dropna().tail(60)
                if len(daily)>=20:
                    r=daily.mul(pd.Series(weights)).sum(axis=1)
                    risk.update({"risk_bars":len(r),"annual_volatility_pct":float(r.std()*np.sqrt(252)*100),
                        "var95_daily_cny":float(np.quantile(r,.05)*info["equity"]),
                        "expected_shortfall95_cny":float(r[r<=np.quantile(r,.05)].mean()*info["equity"])})
                else: risk["risk_missing"]="共同有效日收益不足20行，不生成VaR/波动率"
            result["data"]["risk"]=risk
        return result

    def rebalance_plan(self, name, dataset_id, max_weight=.3):
        if not 0 < max_weight <= 1: raise DataError("PAPER_WEIGHT", "权重需0至1。")
        analysis = self.analyze(dataset_id)
        portfolio = self.portfolio(name,dataset_id)["data"]
        if portfolio["equity"] is None: raise DataError("PAPER_MARKS", "未覆盖全部持仓，不能计算调仓。")
        ranks = analysis["data"]["ranking"][:3]
        weight = min(max_weight,1/len(ranks)) if ranks else 0
        held = {p["symbol"]:p["quantity"] for p in portfolio["positions"]}
        frame,_ = self.store.load(dataset_id)
        marks = self.marks(frame)
        target = {r["symbol"]:int(portfolio["equity"] * weight / r["close"] / 100)*100 for r in ranks}
        plan = [{"symbol": c, "held":held.get(c,0), "target":target.get(c,0),
                 "difference":target.get(c,0)-held.get(c,0), "reference_price":marks[c]["price"], "data_time":marks[c]["date"]}
                for c in sorted(set(held)|set(target))]
        return {"ok":True,"source":"python_paper_plan","data":{"plan":plan,"target_weight":weight,
            "note":"仅调仓草案，不成交；未扣费/滑点，需在模拟组合页逐笔核对现金、T+1、手数、涨跌停与数据日期。"}}

    def paper_trade(self,name,dataset_id,code,side,quantity,request_id,config=None):
        from .common import symbol
        frame,meta=self.store.load(dataset_id)
        g=frame[frame.symbol==symbol(code)]
        if g.empty: raise DataError("PAPER_SYMBOL","数据集不含此股票。")
        return self.book.trade(name,g.iloc[-1].to_dict(),meta,side,quantity,request_id,BacktestConfig(**(config or {})))

    def predict(self,dataset_id,code,horizon,probability,thesis):
        from .common import symbol
        frame,meta = self.store.load(dataset_id)
        g = frame[frame.symbol == symbol(code)]
        if g.empty: raise DataError("PREDICTION_SYMBOL", "该数据集不含目标股票。")
        return self.book.predict(g.iloc[-1].to_dict(),meta,horizon,probability,thesis)

    def evaluate(self,dataset_id):
        frame,meta = self.store.load(dataset_id)
        return self.book.evaluate(frame,meta)

    def daily(self,dataset_id,name=""):
        analysis = self.analyze(dataset_id)
        _,meta = self.store.load(dataset_id)
        portfolio = self.portfolio(name,dataset_id) if name else None
        review = self.evaluate(dataset_id) if not meta["synthetic"] else {"data":{"note":"合成数据不评估真实预测"}}
        journal = self.book.predictions()
        lines = ["# 知衡研究日报", "", f"生成时间：{now()}；行情截止：{meta['end']}；来源：{meta['source']}",
            f"数据集：{dataset_id}；SHA256：{meta['sha256']}",
            "", "本报由Python按固定规则生成，非模型猜测。" + (" **合成示例，非真实市场结论。**" if meta["synthetic"] else ""),
            "", "## 因子观察", "", "|股票|动量%|年化波动%|池内评分|", "|---|---:|---:|---:|"]
        for r in analysis["data"]["ranking"]:
            lines.append(f"|{r['symbol']}|{r['momentum_pct']:.2f}|{r['volatility_pct']:.2f}|{r['score']:.2f}|")
        lines += ["", "## 行业与量价", "", json.dumps(analysis["data"]["sector_rotation"],ensure_ascii=False),
            "量比不代表资金净流入；没有行业映射时不生成行业轮动结论。", "", "## 模拟组合与预测", "",
            json.dumps(portfolio["data"] if portfolio else {"note":"未指定模拟组合"},ensure_ascii=False),
            f"登记预测 {len(journal)} 条；已评估 {sum(bool(p['result']) for p in journal)} 条。",
            json.dumps(review["data"],ensure_ascii=False), "", "## 数据与限制", "", *["- "+x for x in meta["warnings"]],
            "- 固定小股票池观察，不是全市场推荐；日报不保证数据为当日，不自动生成当前新闻。",
            "- 无自动定时服务，刷新数据后手动生成；不连接真实证券账户。"]
        text = "\n".join(lines)
        directory = self.root / "data/quant/daily"
        directory.mkdir(parents=True,exist_ok=True)
        path = directory / (date.today().isoformat()+"-"+hashlib.sha256(text.encode()).hexdigest()[:10]+".md")
        path.write_text(text,encoding="utf-8")
        return {"ok":True,"source":"python_daily_research","data_time":meta["end"],"dataset_id":dataset_id,"synthetic":meta["synthetic"],
                "data":{"report":text,"report_file":str(path)}}


def summarize_backtest(report):
    return {k:v for k,v in report.items() if k!="data"} | {"data":{k:v for k,v in report["data"].items() if k not in {"curve","trades","rejected","signals"}},
        "trade_sample":report["data"]["trades"][:5],"rejected_sample":report["data"]["rejected"][:5]}
