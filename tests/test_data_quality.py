from dataclasses import replace
import json
from unittest.mock import patch

import pandas as pd
import pytest

from investment_agent.common import DataError
from investment_agent.quant_data import DatasetStore,normalize
from investment_agent.data_quality import build_enriched,validate_calendar,guard_corporate_actions
from investment_agent.backtest import BacktestConfig,simulate,run_backtest
from investment_agent.paper import ResearchBook


def bars():
    rows=[]
    for i,d in enumerate(pd.bdate_range("2025-03-03",periods=40)):
        price=10. if i<12 else 9.
        rows.append({"date":str(d.date()),"symbol":"000001.SZ","open":price,"close":price,
            "high":price+.1,"low":price-.1,"volume":1e7,"can_buy":True,"can_sell":True})
    return normalize(pd.DataFrame(rows))


def event(frame):
    return {"symbol":"000001.SZ","record_date":frame.iloc[11].date,"ex_date":frame.iloc[12].date,
        "pay_date":frame.iloc[14].date,"cash_per_share_before_tax":1.,"bonus_per_share":0.,
        "reserve_per_share":0.,"accounting_supported":True}


def metadata(frame,payload=None):
    return {"id":"test","sha256":"test","source":"isolated-fixture","synthetic":False,"adjustment":"raw",
        "start":frame.date.min(),"end":frame.date.max(),"symbols":["000001.SZ"],"provenance":payload or {},"universe_note":"isolated test"}


def test_calendar_must_be_complete_and_duplicate_free():
    rows=[{"calendar_date":"2025-01-01","is_trading_day":"0"},{"calendar_date":"2025-01-02","is_trading_day":"1"}]
    assert validate_calendar(rows,"2025-01-01","2025-01-02")==["2025-01-02"]
    with pytest.raises(DataError,match="完整覆盖"): validate_calendar(rows[:1],"2025-01-01","2025-01-02")
    with pytest.raises(DataError,match="唯一"): validate_calendar(rows+rows,"2025-01-01","2025-01-02")


def test_industry_is_not_backfilled_before_source_update():
    frame=bars()
    payload={"calendar":[{"calendar_date":str(d.date()),"is_trading_day":str(int(d.weekday()<5))}
        for d in pd.date_range(frame.date.min(),frame.date.max())],"industries":[{"code":"sz.000001",
        "industry":"fixture-sector","updateDate":frame.iloc[-2].date}],"dividends":[]}
    enriched,q=build_enriched(frame,metadata(frame),payload)
    assert (enriched.iloc[:-2].sector=="未分类").all()
    assert enriched.iloc[-1].sector=="fixture-sector" and q["calendar"]["sessions"]


def test_known_share_action_blocked_cash_event_allowed():
    frame=bars(); e=event(frame)
    meta=metadata(frame,{"quality":{"corporate_actions":[e]}})
    guard_corporate_actions(meta,BacktestConfig())
    with pytest.raises(DataError): guard_corporate_actions(meta,BacktestConfig(corporate_action_policy="block"))
    e["accounting_supported"]=False; e["bonus_per_share"]=.1
    with pytest.raises(DataError,match="分红送转"): guard_corporate_actions(meta,BacktestConfig())


def test_dividend_eligibility_receivable_and_pay_date():
    frame=bars(); e=event(frame)
    cfg=BacktestConfig(initial_cash=100000,lookback=5,rebalance_days=60,top_n=1,max_weight=1,
        commission_bps=0,min_commission=0,sell_tax_bps=0,slippage_bps=0,impact_bps=0)
    report=simulate(frame,cfg,events=[e])
    assert report["metrics"]["total_return_pct"]==pytest.approx(0.)
    ex=next(r for r in report["curve"] if r["date"]==e["ex_date"])
    pay=next(r for r in report["curve"] if r["date"]==e["pay_date"])
    assert ex["receivables_cny"]==10000 and ex["cash"]==0
    assert pay["receivables_cny"]==0 and pay["cash"]==10000
    assert report["dividend_flows"][0]["cash_cny"]==10000
    # A new holder on ex-date has no record-date entitlement.
    late=simulate(frame,cfg,trade_start=e["ex_date"],events=[e])
    assert late["metrics"]["cash_dividends_cny"]==0


def test_missing_whole_session_is_preserved_in_backtest():
    frame=bars(); calendar=sorted(frame.date.unique()); missing=calendar[20]
    report=simulate(frame[frame.date!=missing],BacktestConfig(lookback=5),calendar=calendar)
    assert missing in [r["date"] for r in report["curve"]]


def test_manifest_calendar_tampering_is_detected(tmp_path):
    store=DatasetStore(tmp_path)
    meta=store.save(bars(),source="synthetic-test",adjustment="raw",synthetic=True)
    path=store.root/(meta["id"]+".json")
    meta["provenance"]={"quality":{"calendar":{"sessions":["2099-01-01"]}}}
    path.write_text(json.dumps(meta),encoding="utf-8")
    with pytest.raises(DataError,match="元信息"): store.load(meta["id"])


def test_prediction_calendar_gap_is_pending_not_shifted(tmp_path):
    book=ResearchBook(tmp_path)
    frame=bars(); meta=metadata(frame)
    with patch("investment_agent.paper.now",return_value="2025-03-03T18:00:00+08:00"):
        book.predict(frame.iloc[0].to_dict(),meta,2,.6,"isolated fixture")
    meta["provenance"]={"quality":{"calendar":{"sessions":["2025-03-04","2025-03-05","2025-03-06"]}}}
    # Missing March 4 must not turn a 2-session horizon into March 6.
    with patch("investment_agent.paper.now",return_value="2025-03-10T18:00:00+08:00"):
        r=book.evaluate(frame[frame.date!="2025-03-04"],meta)
        assert not r["data"]["evaluated"] and r["data"]["pending"]


def test_vendor_limits_enable_strict_simulation_without_choice(tmp_path):
    import asyncio
    from investment_agent.config import Settings
    from investment_agent.service import DataService
    from investment_agent.common import Result
    from investment_agent.data_quality import enrich_execution_limits
    store=DatasetStore(tmp_path); frame=bars()
    meta=store.save(frame.drop(columns=["can_buy","can_sell"]),source="isolated-vendor-fixture",adjustment="raw")
    service=DataService(Settings(root=tmp_path))
    def forbidden(*a,**kw): raise AssertionError("Choice must not be called")
    service.choice.request=forbidden
    async def source(*a,**kw):
        return Result(source="isolated-mcp-fixture",data={"rows":[{"date":d,"symbol":"000001.SZ","limit_up":11.,"limit_down":8.}
            for d in frame.date]})
    service.mx.mcp=source
    result=asyncio.run(enrich_execution_limits(service,meta["id"]))
    enriched,manifest=store.load(result["data"]["id"])
    report=simulate(enriched,BacktestConfig(lookback=5))
    assert report["trades"] and manifest["flags"]["limit_up"]


def test_partial_limit_coverage_is_not_silently_saved(tmp_path):
    import asyncio
    from investment_agent.config import Settings
    from investment_agent.service import DataService
    from investment_agent.common import Result
    from investment_agent.data_quality import enrich_execution_limits
    store=DatasetStore(tmp_path); frame=bars()
    meta=store.save(frame,source="isolated-vendor-fixture",adjustment="raw")
    service=DataService(Settings(root=tmp_path))
    async def source(*a,**kw):
        return Result(source="isolated-mcp-fixture",data={"rows":[{"date":frame.date.iloc[0],"symbol":"000001.SZ","limit_up":11.,"limit_down":8.}]})
    service.mx.mcp=source
    with pytest.raises(DataError,match="未覆盖全部"): asyncio.run(enrich_execution_limits(service,meta["id"]))
    assert len(store.list())==1
