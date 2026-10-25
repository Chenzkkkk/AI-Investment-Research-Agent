"""Versioned daily research snapshots. No invented or interpolated market bars."""
from datetime import date
import hashlib
import io
import json
import re
from pathlib import Path

import numpy as np
import pandas as pd

from .common import DataError, now, symbol, number

ALIASES = {
    "date": ["date", "dates", "日期", "交易日期", "交易日"],
    "symbol": ["symbol", "code", "codes", "股票代码", "证券代码"],
    "open": ["open", "开盘", "开盘价"], "high": ["high", "最高", "最高价"],
    "low": ["low", "最低", "最低价"], "close": ["close", "收盘", "收盘价"],
    "volume": ["volume", "成交量", "成交量(股)", "成交量（股）"],
    "amount": ["amount", "成交额", "成交额(元)", "成交额（元）"],
    "tradable": ["tradable", "tradestatus"],
    "limit_up":["limit_up","涨停价","涨停价格"],
    "limit_down":["limit_down","跌停价","跌停价格"],
}
REQUIRED = {"date", "symbol", "open", "high", "low", "close", "volume"}


def normalize(frame: pd.DataFrame) -> pd.DataFrame:
    frame = frame.copy()
    # Conflicting code columns must not be silently relabeled with the requested asset.
    if "symbol" in frame and "code" in frame:
        def canonical(x):
            text = str(x)
            return symbol(text[3:] + "." + text[:2] if re.fullmatch(r"(?:sh|sz|bj)\.\d{6}",text,re.I) else text)
        if any(canonical(a) != canonical(b) for a,b in zip(frame.symbol,frame.code)):
            raise DataError("OHLCV_SYMBOL", "来源返回的证券代码与请求代码冲突。")
    for target, aliases in ALIASES.items():
        found = next((c for c in frame.columns if str(c).lower().strip() in aliases), None)
        if found is not None and target not in frame:
            frame = frame.rename(columns={found: target})
    if not REQUIRED.issubset(frame.columns):
        raise DataError("OHLCV_SCHEMA", "历史数据缺少字段：" + ",".join(sorted(REQUIRED - set(frame.columns))))
    if frame.empty or len(frame) > 20000:
        raise DataError("OHLCV_SIZE", "数据集必须含1至20000行。")
    try:
        frame["date"] = frame["date"].map(lambda x: date.fromisoformat(str(x)[:10]).isoformat())
        frame["symbol"] = frame["symbol"].map(lambda x: symbol(str(x)[3:] + "." + str(x)[:2]
            if re.fullmatch(r"(?:sh|sz|bj)\.\d{6}", str(x), re.I) else str(x)))
    except (ValueError, TypeError):
        raise DataError("OHLCV_DATE", "日期/证券代码格式无效。") from None
    if (frame.date > date.today().isoformat()).any() or frame.duplicated(["date", "symbol"]).any():
        raise DataError("OHLCV_KEYS", "历史数据包含未来日期或重复日期与证券键。")
    if (pd.to_datetime(frame.date).dt.dayofweek >= 5).any():
        raise DataError("OHLCV_CALENDAR", "A股日线含周末日期，需检查交易日期。")
    if now()[11:13] < "16" and (frame.date == now()[:10]).any():
        raise DataError("OHLCV_OPEN_BAR", "当日日线须在北京时间16时后使用，避免将未收盘数据当完整日线。")
    if "available_at" in frame:
        for value in frame.available_at.dropna():
            try:
                stamp = pd.Timestamp(value)
                if stamp.tzinfo is None: raise ValueError("Timezone required")
            except (ValueError, TypeError):
                raise DataError("PIT_TIMEZONE", "available_at必须是带时区的真实可用时间。") from None
    for col in ["open", "high", "low", "close", "volume"]:
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
        if not np.isfinite(frame[col]).all():
            raise DataError("OHLCV_VALUE", col + " 含缺失或非有限值；未填补。")
    if (frame[["open", "high", "low", "close"]] <= 0).any().any() or (frame.volume < 0).any():
        raise DataError("OHLCV_PRICE", "价格必须为正，成交量不能为负。")
    if ((frame.high < frame[["open", "close", "low"]].max(axis=1)) |
        (frame.low > frame[["open", "close", "high"]].min(axis=1))).any():
        raise DataError("OHLCV_INVARIANT", "最高/最低价与开收盘不一致。")
    for col in ["amount", "preclose", "limit_up", "limit_down", "pe_ttm", "roe_pct"]:
        if col in frame:
            frame[col] = frame[col].map(number)
    for col in ["tradable", "can_buy", "can_sell"]:
        if col in frame:
            def boolean(x):
                if str(x).lower() in {"1", "true", "1.0"}: return True
                if str(x).lower() in {"0", "false", "0.0"}: return False
                raise DataError("OHLCV_FLAGS", col + " 必须逐行明确为0或1。")
            frame[col] = frame[col].map(boolean)
    return frame.sort_values(["date", "symbol"]).reset_index(drop=True)


def extract_rows(payload, code: str, required=None) -> list[dict]:
    """Accept structured JSON/tables only; never infer prices from prose."""
    candidates = []
    required=set(required or REQUIRED)
    native = {}
    def walk(obj):
        if isinstance(obj, list):
            if obj and all(isinstance(x, dict) for x in obj):
                cols = {str(k).lower() for x in obj[:2] for k in x}
                if any(k in cols for key in required-{"date","symbol"} for k in ALIASES.get(key,[key])): candidates.append(obj)
            for x in obj: walk(x)
        elif isinstance(obj, dict):
            columns, items=obj.get("columns"),obj.get("items")
            if isinstance(columns,list) and columns and isinstance(items,list) and code in str(columns[0]):
                for item in items:
                    if not isinstance(item,list) or len(item)!=len(columns): continue
                    label=str(item[0]).strip()
                    if any(x in label for x in ["前复权","后复权","区间"]): continue
                    field=next((key for key in ["open","high","low","close","volume","amount","limit_up","limit_down"]
                        if any(label==alias or label.startswith(alias+"(") or label.startswith(alias+"（")
                               for alias in ALIASES[key])),None)
                    if not field: continue
                    for stamp,value in zip(columns[1:],item[1:]):
                        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}(?:\(日\)|（日）)?",str(stamp)): continue
                        text=str(value).strip()
                        if field=="volume" and "手" in (label+text):
                            numeric=number(text.replace("手",""))
                            numeric=None if numeric is None else numeric*100
                        else:
                            numeric=number(text.replace("元/股",""))
                        if numeric is None: continue
                        row=native.setdefault(str(stamp)[:10],{"date":str(stamp)[:10],"symbol":code})
                        if field in row and row[field]!=numeric:
                            raise DataError("MCP_BAR_CONFLICT","妙想同日同指标存在不同值，未静默覆盖。")
                        row[field]=numeric
            for v in obj.values(): walk(v)
        elif isinstance(obj, str):
            for raw in [obj] + re.findall(r"```(?:json)?\s*([\s\S]*?)```", obj):
                try: walk(json.loads(raw))
                except (ValueError, RecursionError): pass
    walk(payload)
    if native:
        native_rows=list(native.values())
        if all(required.issubset(row) for row in native_rows): candidates.append(native_rows)
    if not candidates:
        raise DataError("NO_STRUCTURED_BARS", "来源未返回可校验的结构化OHLCV；不从文字编造日线。可导入CSV。")
    rows = max(candidates, key=len)
    output=[]
    for row in rows:
        if not any(str(k).lower().strip() in ALIASES["symbol"] for k in row): row={"symbol":code,**row}
        output.append(row)
    return output


class DatasetStore:
    def __init__(self, root: Path):
        self.root = Path(root) / "data/quant/datasets"
        self.root.mkdir(parents=True, exist_ok=True)

    def save(self, frame: pd.DataFrame, *, source: str, adjustment: str, synthetic=False,
             provenance=None, universe_note="用户固定股票池，非历史全市场/指数成分") -> dict:
        if adjustment not in {"raw", "forward", "backward"} or not source.strip():
            raise DataError("DATA_CONTRACT", "必须明确数据来源和复权口径raw/forward/backward。")
        frame = normalize(frame)
        content = frame.to_csv(index=False, lineterminator="\n").encode("utf-8")
        contract = {"source": source, "adjustment": adjustment, "synthetic": bool(synthetic),
                    "provenance": provenance or {}, "universe_note": universe_note}
        digest = hashlib.sha256(content + json.dumps(contract, sort_keys=True, ensure_ascii=True).encode()).hexdigest()
        dataset_id = digest[:20]
        manifest = {"id": dataset_id, "sha256": hashlib.sha256(content).hexdigest(), **contract,
            "fetched_at": now(), "timezone": "Asia/Shanghai", "frequency": "daily", "currency": "CNY",
            "units": {"prices": "元/股", "volume": "股", "amount": "元"}, "rows": len(frame),
            "symbols": sorted(frame.symbol.unique().tolist()), "start": frame.date.min(), "end": frame.date.max(),
            "flags": {x: x in frame for x in ["tradable", "can_buy", "can_sell", "limit_up", "limit_down"]},
            "coverage_by_symbol": {c:{"rows":len(g),"start":g.date.min(),"end":g.date.max(),
                "missing_union_dates":len(set(frame.date)-set(g.date)),"zero_volume_rows":int((g.volume==0).sum())}
                for c,g in frame.groupby("symbol")},
            "warnings": ["未自动重建历史成分与退市样本，存在股票池选择偏差。",
                         "未补齐交易日或缺失日线；停牌与缺失必须区别解释。",
                         "不包含已核验的分红送转事件表；价格收益不等于含息总收益。"]}
        quality=(provenance or {}).get("quality")
        if quality:
            manifest["warnings"]=["固定股票池，历史全市场成分与退市清算未覆盖。",
                "已附来源交易日历，缺失交易日日线不插值；详见quality.coverage。",
                "公司行动按来源事件处理，税前纯现金分红可模拟；个人红利税、送转、配股不自动假定已覆盖。"]
        csv_path, meta_path = self.root / (dataset_id + ".csv"), self.root / (dataset_id + ".json")
        if not meta_path.exists():
            csv_path.write_bytes(content)
            meta_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        return json.loads(meta_path.read_text(encoding="utf-8"))

    def load(self, dataset_id: str):
        if not re.fullmatch(r"[0-9a-f]{20}", dataset_id):
            raise DataError("DATASET_ID", "请选择已保存的数据集编号。")
        meta_path = self.root / (dataset_id + ".json")
        if not meta_path.exists(): raise DataError("DATASET_MISSING", "数据集不存在。")
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        raw = (self.root / (dataset_id + ".csv")).read_bytes()
        if hashlib.sha256(raw).hexdigest() != meta["sha256"]:
            raise DataError("DATASET_CHANGED", "数据快照哈希不匹配，拒绝使用。")
        contract={k:meta[k] for k in ["source","adjustment","synthetic","provenance","universe_note"]}
        if hashlib.sha256(raw+json.dumps(contract,sort_keys=True,ensure_ascii=True).encode()).hexdigest()[:20]!=dataset_id:
            raise DataError("DATASET_METADATA_CHANGED","快照来源/日历等元信息与编号不匹配，拒绝使用。")
        return normalize(pd.read_csv(io.BytesIO(raw), dtype={"symbol": str})), meta

    def list(self) -> list[dict]:
        return sorted([json.loads(p.read_text(encoding="utf-8")) for p in self.root.glob("*.json")],
                      key=lambda x:(x["end"],x["rows"],x["fetched_at"]),reverse=True)


async def collect_history(service, codes: list[str], start: str, end: str, source="auto") -> dict:
    from .common import Result
    import asyncio
    codes = list(dict.fromkeys(symbol(c) for c in codes))
    begin, finish = date.fromisoformat(start), date.fromisoformat(end)
    if not 1 <= len(codes) <= 5 or begin > finish or (finish - begin).days > 365 or finish > date.today():
        raise DataError("HISTORY_RANGE", "最多5只股票、365个日历日，不能查询未来。")
    if source not in {"auto", "public", "miaoxiang", "choice"}: raise DataError("INVALID_SOURCE", "未知来源。")
    sources = [source] if source != "auto" else [s for s in ["choice", "public", "miaoxiang"] if service.enabled(s)]
    frames, provenance, failures = [], [], []
    for code in codes:
        accepted = False
        for selected in sources:
            args = {"quant_ohlcv_v": 2, "code": code, "start": start, "end": end}
            if selected == "miaoxiang":
                async def action():
                    return await service.mx.mcp("mx_ashare_finance_data",
                        f"仅查询{code}在{start}至{end}每个交易日的不复权日线：开盘价、最高价、最低价、收盘价（元/股）、"
                        "成交量（股）、成交额（元）。保留交易日期和单位，可用原生数据表。不要公司简介、估值、技术指标或资金流。"
                        "必须同时含每日最高价最低价成交量；缺少请明确返回缺失，不要补造数据。", code, "history")
            elif selected == "public":
                async def action(): return await service.public.request(code, "history", start, end)
            else:
                async def action(): return await asyncio.to_thread(service.choice.request, "ohlcv", code, start, end)
            r = await service.fetch(selected, args, action, ttl=86400)
            try:
                if not r.ok: raise DataError("SOURCE_FAILED", str(r.error))
                frame = normalize(pd.DataFrame(extract_rows(r.data, code)))
                frame = frame[(frame.date >= start) & (frame.date <= end) & (frame.symbol == code)]
                if len(frame) < 2: raise DataError("SHORT_HISTORY", "返回不足两个交易日。")
                frames.append(frame)
                provenance.append({"symbol": code, "source": r.source, "fetched_at": r.fetched_at,
                                   "data_time": r.data_time, "cached": r.cached, "rows": len(frame)})
                accepted = True
                break
            except DataError as e:
                failures.append({"source": selected, "symbol": code, "code": e.code, "message": str(e)})
        if not accepted:
            raise DataError("HISTORY_UNAVAILABLE", "未完整获取股票池历史数据；不静默删掉失败股票。" + json.dumps(failures, ensure_ascii=False))
    joined = pd.concat(frames, ignore_index=True)
    for col in ["tradable","can_buy","can_sell"]:
        if col in joined and joined[col].isna().any():
            joined=joined.drop(columns=[col])
            failures.append({"column":col,"message":"来源间标志覆盖不完整，整体缺省并保留严格成交限制。"})
    manifest = DatasetStore(service.settings.root).save(joined, source="+".join(sorted({x["source"] for x in provenance})),
        adjustment="raw", provenance={"requests": provenance, "failures": failures, "requested_start": start, "requested_end": end})
    return {"ok": True, "source": "historical_snapshot", "data_time": manifest["end"], "data": manifest}
