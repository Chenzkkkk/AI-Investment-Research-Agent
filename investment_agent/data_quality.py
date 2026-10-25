"""Optional sourced calendar and corporate-action quality gate. Choice is never used."""
from datetime import date, timedelta
import hashlib
import json
import re

import pandas as pd

from .common import DataError, now, symbol, number
from .quant_data import DatasetStore,extract_rows


def source_symbol(code):
    return symbol(code[3:]+"."+code[:2] if len(code)==9 and code[2]=="." else code)


def validate_calendar(rows, start, end):
    dates={}
    for row in rows:
        d=date.fromisoformat(str(row.get("calendar_date",""))).isoformat()
        flag=str(row.get("is_trading_day",""))
        if not start<=d<=end or flag not in {"0","1"} or d in dates:
            raise DataError("CALENDAR_SCHEMA","交易日历日期/标志/唯一键无效。")
        dates[d]=flag
    expected={str(d.date()) for d in pd.date_range(start,end)}
    if set(dates)!=expected: raise DataError("CALENDAR_INCOMPLETE","来源日历没有完整覆盖请求的日历日，拒绝冒充完整交易日历。")
    return sorted(d for d,flag in dates.items() if flag=="1")


def build_enriched(frame,meta,payload):
    start,end=meta["start"],meta["end"]
    days=validate_calendar(payload["calendar"],start,end)
    if not set(frame.date).issubset(days): raise DataError("NON_SESSION_BAR","日线中存在来源日历标记为休市的日期。")
    coverage={code:{"missing_sessions":sorted(set(days)-set(g.date)),"zero_volume_days":g.loc[g.volume==0,"date"].tolist()}
              for code,g in frame.groupby("symbol")}
    events=[]
    for raw in payload.get("dividends",[]):
        try:
            code=source_symbol(raw["code"])
            ex=date.fromisoformat(raw.get("dividOperateDate","")).isoformat()
        except (KeyError,ValueError,DataError): continue
        if code not in meta["symbols"] or not start<=ex<=end: continue
        cash=number(raw.get("dividCashPsBeforeTax"))
        bonus=number(raw.get("dividStocksPs"))
        reserve=number(raw.get("dividReserveToStockPs"))
        # Preserve source strings; an empty amount is unknown, never assumed zero.
        cash_only=bonus==0 and (reserve==0 or (reserve is None and bool(re.fullmatch(r"10派[\d.]+元(?:（[^）]*）)?",raw.get("dividCashStock","")))))
        try:
            record=date.fromisoformat(raw.get("dividRegistDate","")).isoformat()
            pay=date.fromisoformat(raw.get("dividPayDate","")).isoformat()
            valid_dates=record<ex<=pay
        except (ValueError,TypeError): valid_dates=False
        events.append({"symbol":code,"ex_date":ex,"record_date":raw.get("dividRegistDate"),
            "pay_date":raw.get("dividPayDate"),"share_listing_date":raw.get("dividStockMarketDate"),
            "cash_per_share_before_tax":cash,"bonus_per_share":bonus,"reserve_per_share":reserve,
            "original":raw,"accounting_supported":bool(cash_only and valid_dates and cash is not None and cash>=0)})
    keys=[(e["symbol"],e["ex_date"],e["record_date"]) for e in events]
    if len(keys)!=len(set(keys)): raise DataError("DUPLICATE_ACTION","公司行动事件存在重复键，拒绝重复计算分红。")
    joined=frame.copy()
    industry_records=[]
    for raw in payload.get("industries",[]):
        try:
            code=source_symbol(raw["code"])
            effective=date.fromisoformat(raw.get("updateDate","")).isoformat()
        except (KeyError,ValueError,DataError): continue
        if code not in meta["symbols"]: continue
        accepted=bool(raw.get("industry")) and effective<=end
        industry_records.append({"symbol":code,"effective_date":effective,"industry":raw.get("industry"),"accepted":accepted})
        # Never backfill a modern classification into older bars.
        if accepted: joined.loc[(joined.symbol==code)&(joined.date>=effective),"sector"]=raw["industry"]
    if "sector" in joined: joined["sector"]=joined.sector.fillna("未分类")
    quality={"checked_at":now(),"source":"public_baostock","calendar":{"start":start,"end":end,"sessions":days,
        "sha256":hashlib.sha256(json.dumps(payload["calendar"],sort_keys=True).encode()).hexdigest()},
        "coverage":coverage,"corporate_actions":events,"industries":industry_records,
        "source_errors":payload.get("errors",[]),"provider_subrequests":payload.get("subrequests"),
        "limitations":["行业只从来源更新日开始使用，不重建此前版本。","回测可处理已验证日期的税前纯现金分红；送转/配股仍阻断，事件不自动写入模拟簿。",
                        "没有历史全市场成分与退市清算覆盖，仍限定固定股票池。"]}
    return joined,quality


async def enrich_dataset(service,dataset_id):
    store=DatasetStore(service.settings.root)
    frame,meta=store.load(dataset_id)
    if meta["synthetic"]: raise DataError("QUALITY_SYNTHETIC","合成数据不联网匹配真实交易日历/分红。")
    args={"metadata_version":1,"symbols":meta["symbols"],"start":meta["start"],"end":meta["end"]}
    result=await service.fetch("public",args,lambda:service.public.request(meta["symbols"][0],"research_metadata",
        meta["start"],meta["end"],codes=meta["symbols"]),ttl=86400)
    if not result.ok: raise DataError("QUALITY_SOURCE",str(result.error))
    frame,quality=build_enriched(frame,meta,result.data)
    quality.update({"provider_fetched_at":result.fetched_at,"provider_cached":result.cached,
                    "payload_sha256":hashlib.sha256(json.dumps(result.data,sort_keys=True,ensure_ascii=True).encode()).hexdigest()})
    lineage={**meta.get("provenance",{}),"parent_dataset_id":dataset_id,"quality":quality}
    enriched=store.save(frame,source=meta["source"],adjustment=meta["adjustment"],synthetic=False,
                        provenance=lineage,universe_note=meta["universe_note"])
    return {"ok":True,"source":"python_data_quality","data_time":meta["end"],"data":{"dataset":enriched,"quality":quality}}


def quality_of(meta): return meta.get("provenance",{}).get("quality",{})


def sessions_for(meta):
    return quality_of(meta).get("calendar",{}).get("sessions")


async def enrich_execution_limits(service,dataset_id):
    """Require explicit historical limits from the vendor; never infer a universal 10%."""
    store=DatasetStore(service.settings.root)
    frame,meta=store.load(dataset_id)
    if meta["synthetic"]: raise DataError("LIMITS_SYNTHETIC","合成数据不匹配真实证券涨跌停。")
    proofs=[]
    joined=frame.drop(columns=[c for c in ["limit_up","limit_down"] if c in frame]).copy()
    tables=[]
    for code in meta["symbols"]:
        args={"limits_version":1,"symbol":code,"start":meta["start"],"end":meta["end"]}
        result=await service.fetch("miaoxiang",args,lambda:service.mx.mcp("mx_ashare_finance_data",
            f"仅查询{code}从{meta['start']}至{meta['end']}每个交易日的涨停价和跌停价，单位元/股，不是最高价和最低价。"
            "请保留逐日日期及原生数据表，只返回历史涨停价、跌停价；无涨跌幅限制或字段不支持时明确缺失，不要用10%推算，不要用今天价格填历史。",code,"history"),ttl=86400)
        if not result.ok: raise DataError("LIMITS_SOURCE",str(result.error))
        rows=extract_rows(result.data,code,required={"date","symbol","limit_up","limit_down"})
        table=pd.DataFrame(rows)
        for col in ["limit_up","limit_down"]:
            if col not in table: raise DataError("LIMITS_MISSING","来源未提供历史涨跌停价格。")
            table[col]=table[col].map(number)
        table["date"]=table.date.astype(str).str[:10]
        table["symbol"]=table.symbol.map(source_symbol)
        table=table[(table.symbol==code)&(table.date>=meta["start"])&(table.date<=meta["end"])]
        if table.empty or table.duplicated(["date","symbol"]).any(): raise DataError("LIMITS_MISSING","无可唯一匹配的历史涨跌停价格。")
        if table[["limit_up","limit_down"]].isna().any().any() or (table.limit_down<=0).any() or (table.limit_up<=table.limit_down).any():
            raise DataError("LIMITS_INVALID","历史涨跌停字段有缺失或矛盾，不推算替代。")
        tables.append(table[["date","symbol","limit_up","limit_down"]])
        proofs.append({"symbol":code,"source":result.source,"fetched_at":result.fetched_at,"cached":result.cached,"rows":len(table)})
    joined=joined.merge(pd.concat(tables),on=["date","symbol"],how="left",validate="one_to_one")
    covered=joined.limit_up.notna()&joined.limit_down.notna()
    if not covered.all(): raise DataError("LIMITS_INCOMPLETE","涨跌停价格未覆盖全部股票与日期，未生成号称完整的新快照。")
    if ((joined.open>joined.limit_up+.011)|(joined.open<joined.limit_down-.011)).any():
        raise DataError("LIMITS_CONFLICT","涨跌停价格与同日不复权开盘价冲突，拒绝入库。")
    lineage={**meta.get("provenance",{}),"limits_parent_dataset_id":dataset_id,"execution_limits":{"source":"miaoxiang_mcp","requests":proofs}}
    enriched=store.save(joined,source=meta["source"]+"+miaoxiang_limits",adjustment=meta["adjustment"],provenance=lineage,
        universe_note=meta["universe_note"])
    return {"ok":True,"source":"python_execution_limits","data_time":meta["end"],"data":enriched}


def guard_corporate_actions(meta,config):
    quality=quality_of(meta)
    events=quality.get("corporate_actions",[])
    unsupported=[e for e in events if not e.get("accounting_supported")]
    blocked=events if config.corporate_action_policy=="block" else unsupported
    if blocked and config.corporate_action_policy!="price_only":
        labels=", ".join(x["symbol"]+" "+x["ex_date"] for x in blocked[:8])
        raise DataError("CORPORATE_ACTION_UNMODELED","样本含已发现但未建模的分红送转事件："+labels+
            "。已阻断按含息收益解读；如仅研究未复权价格路径，须明确选择price_only并保留事件提示。")
    return quality
