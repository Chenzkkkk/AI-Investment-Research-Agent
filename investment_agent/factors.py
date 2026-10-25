"""Causal technical/cross-sectional factors; fundamentals require available_at."""
import numpy as np
import pandas as pd
from .common import DataError


def factor_table(frame: pd.DataFrame, as_of: str = "", lookback=20) -> dict:
    if not 5 <= lookback <= 120: raise DataError("LOOKBACK", "回看窗口需5至120日。")
    as_of = as_of or frame.date.max()
    rows = []
    for code, group in frame[frame.date <= as_of].groupby("symbol", sort=True):
        g = group.sort_values("date")
        if len(g) < lookback + 1:
            rows.append({"symbol": code, "eligible": False, "reason": "历史长度不足", "bars": len(g)})
            continue
        close = g.close.astype(float)
        returns = close.pct_change().tail(lookback)
        latest = g.iloc[-1]
        row = {"symbol": code, "eligible": latest.date == as_of, "data_time": latest.date, "bars": len(g),
            "close": float(close.iloc[-1]), "momentum_pct": float((close.iloc[-1] / close.iloc[-1-lookback] - 1) * 100),
            "volatility_pct": float(returns.std(ddof=1) * np.sqrt(252) * 100),
            "ma_gap_pct": float((close.iloc[-1] / close.tail(lookback).mean() - 1) * 100),
            "window_drawdown_pct": float((1 - (close.tail(lookback + 1) / close.tail(lookback + 1).cummax()).min()) * 100),
            "volume_ratio": float(g.volume.iloc[-1] / g.volume.iloc[-lookback-1:-1].mean()) if g.volume.iloc[-lookback-1:-1].mean() > 0 else None,
            "sector": str(latest.get("sector", "未分类"))}
        if len(g)>=15:
            delta=close.diff().tail(14)
            gain,loss=delta.clip(lower=0).mean(),-delta.clip(upper=0).mean()
            row["rsi14_simple"] = float(100-100/(1+gain/loss)) if loss>0 else (100. if gain>0 else 50.)
        if len(g)>=26:
            dif=close.ewm(span=12,adjust=False).mean()-close.ewm(span=26,adjust=False).mean()
            dea=dif.ewm(span=9,adjust=False).mean()
            row.update({"macd_dif":float(dif.iloc[-1]),"macd_dea":float(dea.iloc[-1]),
                        "macd_histogram":float(2*(dif.iloc[-1]-dea.iloc[-1]))})
        if "amount" in g and g.amount.tail(lookback).notna().all():
            row["mean_amount_cny"]=float(g.amount.tail(lookback).mean())
        if latest.date != as_of: row["reason"] = "目标日缺失，未用陈旧价格排名"
        # Each vintage is independently timestamped; financial period-end alone is insufficient.
        if {"available_at", "pe_ttm", "roe_pct"}.issubset(g.columns):
            available = pd.to_datetime(g.available_at, utc=True, errors="coerce")
            cutoff = pd.Timestamp(as_of + " 15:00", tz="Asia/Shanghai").tz_convert("UTC")
            pit = g[available <= cutoff].copy()
            if len(pit):
                pit["_available"] = available[available <= cutoff]
                v = pit.sort_values(["_available", "date"]).iloc[-1]
                if pd.notna(v.pe_ttm) and v.pe_ttm > 0: row["earnings_yield"] = float(1 / v.pe_ttm)
                if pd.notna(v.roe_pct): row["quality_roe_pct"] = float(v.roe_pct)
        rows.append(row)
    eligible = [r for r in rows if r["eligible"]]
    if eligible:
        table = pd.DataFrame(eligible).set_index("symbol")
        # Equal weights predeclared, daily cross section only. Missing PIT values do not get zero/imputed quality.
        components = [("momentum_pct", True), ("volatility_pct", False), ("ma_gap_pct", True)]
        for col in ["earnings_yield", "quality_roe_pct"]:
            if col in table and table[col].notna().all(): components.append((col, True))
        score = sum(table[col].rank(pct=True, ascending=ascending) for col, ascending in components) / len(components) * 100
        for r in eligible: r["score"] = float(score.loc[r["symbol"]])
        eligible.sort(key=lambda x: (-x["score"], x["symbol"]))
    sectors = []
    for sector in sorted({r["sector"] for r in eligible if r["sector"] != "未分类"}):
        members = [r for r in eligible if r["sector"] == sector]
        sectors.append({"sector": sector, "members": len(members),
                        "mean_momentum_pct": float(np.mean([r["momentum_pct"] for r in members])),
                        "positive_breadth_pct": sum(r["momentum_pct"] > 0 for r in members) / len(members) * 100})
    sectors.sort(key=lambda x: -x["mean_momentum_pct"])
    return {"as_of": as_of, "lookback": lookback, "ranking": eligible,
            "excluded": [r for r in rows if not r["eligible"]], "sector_rotation": sectors,
            "formula": "等权横截面百分位：动量、低波动、均线偏离；仅全体具备PIT字段时加入盈利收益率与ROE。RSI为14日简单均值版，MACD为EMA12/26及9日信号线。",
            "limitations": ["小股票池排名仅在本池内有效，不是全A股排名。", "行业为导入映射，历史分类未重建。",
                             "量比仅反映成交活跃度，不冒充主力资金净流入。", "252交易日年化；财务值必须含真实available_at。"]}
