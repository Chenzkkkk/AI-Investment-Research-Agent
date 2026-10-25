"""Lazy, disabled-by-default Choice adapter. Read-only SDK calls only."""
from datetime import date, timedelta
import json
from pathlib import Path
import threading
from typing import Callable

from .common import DataError, Result, symbol, number
from .config import Settings

LOCK = threading.RLock()
AUTH_CODES = {10001003, 10001004, 10001007, 10001008, 10001012, 10001019, 10001020, 10001025}


class ChoiceProvider:
    def __init__(self, settings: Settings, loader: Callable | None = None):
        self.settings = settings
        self.loader = loader
        self.denied: int | None = None

    def _sdk(self):
        if not self.settings.enable_choice:
            raise DataError("DISABLED", "Choice 已关闭；权限开通后设置 ENABLE_CHOICE=true 并重启。")
        if self.denied:
            raise DataError("CHOICE_AUTH_BLOCKED", f"Choice 权限错误 {self.denied}；本进程已停止重试。")
        if self.loader:
            return self.loader()
        from EmQuantAPI import c, UtilAccess
        if not (Path(UtilAccess.GetLibraryPath()).parent / "userInfo").exists():
            raise DataError("ACTIVATION_REQUIRED", "缺少 Choice userInfo，请使用官方工具激活。")
        return c

    def _check(self, result) -> None:
        code = int(getattr(result, "ErrorCode", 0))
        if code:
            if code in AUTH_CODES:
                self.denied = code
            raise DataError("CHOICE_" + str(code), f"Choice 返回错误码 {code}；未生成数据。")

    def _serialize(self, result) -> dict:
        import pandas as pd
        if isinstance(result, pd.DataFrame):
            if result.empty or result.isna().all().all():
                raise DataError("EMPTY_DATA", "Choice 返回空数据。")
            return {"rows": json.loads(result.reset_index().to_json(orient="records", date_format="iso", force_ascii=False))}
        self._check(result)
        data = getattr(result, "Data", None)
        if not data:
            raise DataError("EMPTY_DATA", "Choice 返回空数据。")
        return {"codes": list(getattr(result, "Codes", [])), "indicators": list(getattr(result, "Indicators", [])),
                "dates": [str(d) for d in getattr(result, "Dates", [])], "values": data}

    def request(self, operation: str, code: str = "600519.SH", start: str = "", end: str = "", **kwargs) -> Result:
        if operation not in {"quote", "fundamentals", "history", "ohlcv", "calendar", "macro", "news", "screen", "report", "usage"}:
            raise DataError("TOOL_NOT_ALLOWED", "Choice 仅开放已登记的只读研究功能。")
        normalized = symbol(code)
        finish = date.fromisoformat(end) if end else date.today()
        begin = date.fromisoformat(start) if start else finish - timedelta(days=7)
        if begin > finish or (finish - begin).days > (365 if operation == "ohlcv" else 31) or finish > date.today():
            raise DataError("RANGE_LIMIT", "首次使用限定最近至多 31 天，且不能查询未来。")
        with LOCK:
            c = self._sdk()
            login = c.start("RecordLoginInfo=0,ForceLogin=0,HTTPTimeout=15", logcallback=lambda _: 1)
            self._check(login)
            try:
                stamp = finish.strftime("%Y%m%d")
                if operation == "quote":
                    # Official demo.py documents NOW snapshot and TIME real-time indicators.
                    r = c.csqsnapshot(normalized, "NOW,TIME", "Ispandas=0")
                    units = {"NOW": "元/股，快照最新成交价", "TIME": "来源行情时钟，保留原值"}
                elif operation == "fundamentals":
                    r = c.css(normalized, "PETTM,PBMRQN", f"TradeDate={stamp},Ispandas=1")
                    units = {"PETTM": "倍，TTM", "PBMRQN": "倍，MRQ按最近公告日"}
                elif operation == "history":
                    r = c.csd(normalized, "CLOSE", begin.isoformat(), finish.isoformat(),
                              "Period=1,AdjustFlag=1,Order=1,Ispandas=1")
                    units = {"CLOSE": "元/股，不复权"}
                elif operation == "ohlcv":
                    r = c.csd(normalized, "OPEN,HIGH,LOW,CLOSE,VOLUME,AMOUNT", begin.isoformat(), finish.isoformat(),
                              "Period=1,AdjustFlag=1,Order=1,Ispandas=0")
                    self._check(r)
                    indicators = [str(x).lower() for x in r.Indicators]
                    values = r.Data.get(normalized, [])
                    if len(values) != len(indicators) or not {"open", "high", "low", "close", "volume", "amount"}.issubset(indicators):
                        raise DataError("CHOICE_SCHEMA", "Choice日线字段不完整，拒绝补造。")
                    rows = [{"date": str(d)[:10], "symbol": normalized,
                             **{k: values[j][i] for j,k in enumerate(indicators)}} for i,d in enumerate(r.Dates)]
                    return Result(source="choice", symbol=normalized, kind="history", data={"rows": rows},
                        data_time=str(r.Dates[-1])[:10] if r.Dates else None,
                        metadata={"adjustment": "raw", "units": {"volume": "股", "amount": "元", "prices": "元/股"},
                                  "live_entitlement_verified": True, "missing": "涨跌停价/停牌/分红送转需单独补充"})
                elif operation == "calendar":
                    r = c.tradedates(begin.isoformat(), finish.isoformat(), "Market=CNSESH,Order=1")
                    units = {"dates": "交易日期"}
                elif operation == "macro":
                    indicator = kwargs.get("indicator", "")
                    # IDs must come from reviewed search results, not model invention.
                    catalog_path = self.settings.root / "data/choice_edb_catalog.json"
                    catalog = json.loads(catalog_path.read_text(encoding="utf-8")) if catalog_path.exists() else {}
                    if indicator not in catalog:
                        raise DataError("INDICATOR_NOT_VERIFIED", "请先使用 choice-edb-search 检索并核对宏观指标。")
                    r = c.edb(indicator, f"StartDate={begin.isoformat()},EndDate={finish.isoformat()},Ispandas=1")
                    units = {indicator: catalog[indicator].get("unit", "未提供")}
                elif operation == "news":
                    r = c.cfn(normalized, "companynews", 2, f"endtime={stamp},count=3")
                    units = {}
                elif operation == "screen":
                    # A bounded universe, official CLOSE parameter syntax; positive-close filter only.
                    r = c.cps(normalized, f"s0,CLOSE,{finish.isoformat()},1;s1,NAME", "[s0]>0", "top=max([s0],1)")
                    units = {"s0": "元/股"}
                elif operation == "report":
                    report_date = kwargs.get("report_date", f"{finish.year - 1}-12-31")
                    if date.fromisoformat(report_date) > finish:
                        raise DataError("INVALID_REPORT_DATE", "不能请求未来报告期。")
                    r = c.ctr("IncomeStatementSHSZ", "REPORTDATE,OPERATEREVE",
                        f"SecuCode={normalized},ReportDate={report_date},ReportType=1,Ispandas=1")
                    units = {"OPERATEREVE": "元，合并利润表营业收入", "REPORTDATE": "报告期"}
                else:
                    r = c.datastatistics("EM_CSS", "", f"StartDate={begin.isoformat()},EndDate={finish.isoformat()},Ispandas=1")
                    units = {}
                data = self._serialize(r)
                if operation == "usage":
                    data = {"original_text": r.to_string() if hasattr(r, "to_string") else str(r), "verbatim_only": True}
                metadata = {"requested_end": finish.isoformat(), "requested_start": begin.isoformat(),
                            "units": units, "cost": "依账户套餐，金额未知", "live_entitlement_verified": True}
                if operation == "quote":
                    raw_values = data.get("values", {}).get(normalized, [])
                    indices = data.get("indicators", [])
                    if "NOW" in indices and len(raw_values) > indices.index("NOW"):
                        price = raw_values[indices.index("NOW")]
                        while isinstance(price, list) and len(price) == 1:
                            price = price[0]
                        if number(price) is not None:
                            metadata["normalized_quote"] = {"price": number(price), "unit": "元/股", "currency": "CNY",
                                "price_basis": "最新价", "data_time": None}
                return Result(source="choice", symbol=normalized, kind=operation, data=data,
                              data_time=None, time_basis="以返回行的 DATES/dates 为准；请求截止日不是行情时间",
                              metadata=metadata)
            finally:
                c.stop()
