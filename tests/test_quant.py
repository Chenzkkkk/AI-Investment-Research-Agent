from dataclasses import replace
from datetime import date, timedelta
import json
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from investment_agent.backtest import BacktestConfig, simulate, run_backtest, execution_block
from investment_agent.common import DataError
from investment_agent.factors import factor_table
from investment_agent.paper import ResearchBook
from investment_agent.quant_data import DatasetStore, normalize
from investment_agent.quant import QuantLab


@pytest.fixture
def panel():
    rows=[]
    for i,d in enumerate(pd.bdate_range("2025-01-02",periods=80)):
        price=10+i*.03+np.sin(i/3)*.02
        rows.append({"date":str(d.date()),"symbol":"000001.SZ","open":price,"close":price+.01,
            "high":price+.1,"low":price-.1,"volume":1e7,"amount":1e8,"tradable":True,
            "can_buy":True,"can_sell":True,"sector":"测试行业"})
    return normalize(pd.DataFrame(rows))


@pytest.fixture
def config(): return BacktestConfig(lookback=5,rebalance_days=3,top_n=1,max_weight=1,initial_cash=100000)


def test_snapshot_rejects_duplicate_impossible_and_unknown_adjustment(panel,tmp_path):
    with pytest.raises(DataError,match="重复"): normalize(pd.concat([panel,panel.iloc[:1]]))
    malformed=panel.copy(); malformed.loc[0,"high"]=.1
    with pytest.raises(DataError,match="不一致"): normalize(malformed)
    store=DatasetStore(tmp_path)
    with pytest.raises(DataError,match="复权"): store.save(panel,source="synthetic-test",adjustment="unknown")
    meta=store.save(panel,source="synthetic-test",adjustment="raw",synthetic=True)
    restored,manifest=store.load(meta["id"])
    assert len(restored)==len(panel) and manifest["synthetic"]
    path=store.root/(meta["id"]+".csv"); path.write_bytes(path.read_bytes()+b"\n")
    with pytest.raises(DataError,match="哈希"): store.load(meta["id"])
    with pytest.raises(DataError): store.load("../escape")


def test_future_sentinel_cannot_change_past_signals_or_fills(panel,config):
    result=simulate(panel,config)
    altered=panel.copy()
    for c in ["open","close","high","low"]: altered.loc[60:,c]*=3
    changed=simulate(altered,config)
    cutoff=panel.iloc[60].date
    assert [t for t in result["trades"] if t["date"]<cutoff]==[t for t in changed["trades"] if t["date"]<cutoff]
    assert [s for s in result["signals"] if s["execution_date"]<cutoff]==[s for s in changed["signals"] if s["execution_date"]<cutoff]
    assert all(t["signal_date"]<t["date"] for t in result["trades"])
    assert all(t["quantity"]%100==0 for t in result["trades"])
    assert all(r["cash"]>=0 for r in result["curve"])


def test_costs_are_deducted_and_double_costs_are_stressed(panel,config,tmp_path):
    flat=panel.copy()
    for key,value in [("open",10),("close",10),("high",11),("low",9)]: flat[key]=value
    meta=DatasetStore(tmp_path).save(flat,source="synthetic-test",adjustment="raw",synthetic=True)
    result=run_backtest(flat,meta,config,tmp_path)
    assert result["data"]["metrics"]["total_return_pct"]<0
    assert result["data"]["metrics"]["fees_cny"]>0
    assert result["sensitivity"][0]["metrics"]["total_return_pct"]<result["data"]["metrics"]["total_return_pct"]
    assert result["holdout"]["split_date"]>result["data"]["curve"][1]["date"]
    assert result["engine_sha256"] and result["trials"]==4


def test_strict_missing_limits_and_suspension_block_trades(panel,config):
    unknown=panel.drop(columns=["can_buy","can_sell"])
    strict=simulate(unknown,config)
    assert not strict["trades"] and strict["rejected"]
    relaxed=simulate(unknown,replace(config,strict_limits=False))
    assert relaxed["trades"]
    paused=panel.copy(); paused["tradable"]=False
    assert not simulate(paused,config)["trades"]
    bar=panel.iloc[6].to_dict(); bar.pop("can_buy"); bar["limit_up"]=bar["open"]
    assert execution_block(bar,"buy",True)


def test_missing_held_bar_preserves_mark_and_position(panel,config):
    missing=panel.drop(index=20)
    # Second ticker provides a calendar date so missing held security is visible.
    other=panel.copy(); other["symbol"]="600000.SH"; other["close"]*=.9
    other["open"]=other["close"]; other["low"]=other["close"]-.1; other["high"]=other["close"]+.1
    result=simulate(pd.concat([missing,other]).sort_values(["date","symbol"]),config)
    row=next(r for r in result["curve"] if r["date"]==panel.iloc[20].date)
    assert "000001.SZ" in row["stale_symbols"]
    assert not any(t["symbol"]=="000001.SZ" and t["date"]==row["date"] for t in result["trades"])


def test_pit_financials_exclude_future_publication(panel):
    panel=panel.copy(); panel["available_at"]="2099-01-01T09:00:00+08:00"
    panel["pe_ttm"]=1.; panel["roe_pct"]=99.
    current=factor_table(panel,lookback=5)["ranking"][0]
    assert "earnings_yield" not in current and "quality_roe_pct" not in current
    panel["available_at"]="2025-01-01T09:00:00+08:00"
    assert factor_table(panel,lookback=5)["ranking"][0]["quality_roe_pct"]==99


def test_adjusted_price_cannot_be_used_for_execution(panel,config,tmp_path):
    meta=DatasetStore(tmp_path).save(panel,source="synthetic-test",adjustment="forward",synthetic=True)
    with pytest.raises(DataError,match="不复权"): run_backtest(panel,meta,config,tmp_path)


def paper_bar(day):
    return {"date":day,"symbol":"000001.SZ","open":10.,"close":10.,"high":11.,"low":9.,
            "volume":1e7,"can_buy":True,"can_sell":True,"tradable":True}


def test_paper_t1_idempotency_cash_and_missing_marks(tmp_path):
    book=ResearchBook(tmp_path)
    book.create("test",10000)
    today=str(date.today()); yesterday=str(date.today()-timedelta(days=1))
    meta={"id":"test-id","sha256":"test-hash","source":"isolated-test-fixture","synthetic":False,"adjustment":"raw"}
    bought=book.trade("test",paper_bar(yesterday),meta,"buy",100,"request-0001")
    repeated=book.trade("test",paper_bar(yesterday),meta,"buy",100,"request-0001")
    assert not bought["idempotent"] and repeated["idempotent"]
    with pytest.raises(DataError,match=r"T\+1"): book.trade("test",paper_bar(yesterday),meta,"sell",100,"request-0002")
    with pytest.raises(DataError,match="现金不足"): book.trade("test",paper_bar(yesterday),meta,"buy",10000,"request-0003")
    assert book.portfolio("test")["data"]["equity"] is None
    with patch("investment_agent.paper.now",return_value=today+"T18:00:00+08:00"):
        book.trade("test",paper_bar(today),meta,"sell",100,"request-0004")
    state=book.portfolio("test")["data"]
    assert not state["positions"] and state["equity"]<10000 and len(state["trades"])==2


def test_prediction_cannot_be_backfilled_or_evaluated_early(tmp_path):
    book=ResearchBook(tmp_path)
    meta={"id":"test-id","sha256":"test-hash","synthetic":False,"adjustment":"raw"}
    with patch("investment_agent.paper.now",return_value="2025-03-10T18:00:00+08:00"):
        pred=book.predict(paper_bar("2025-03-10"),meta,2,.7,"isolated test fixture")
    panel=normalize(pd.DataFrame([paper_bar("2025-03-11"),paper_bar("2025-03-12")]))
    with patch("investment_agent.paper.now",return_value="2025-03-11T18:00:00+08:00"):
        assert not book.evaluate(panel,meta)["data"]["evaluated"]
    with patch("investment_agent.paper.now",return_value="2025-03-12T18:00:00+08:00"):
        result=book.evaluate(panel,meta)["data"]["evaluated"]
        assert len(result)==1 and result[0]["brier_score"]==pytest.approx(.49)
        assert not book.evaluate(panel,meta)["data"]["evaluated"]
    with patch("investment_agent.paper.now",return_value="2025-03-20T18:00:00+08:00"):
        with pytest.raises(DataError,match="过旧"): book.predict(paper_bar("2025-03-10"),meta,2,.7,"test")


def test_tools_accept_local_snapshot_and_daily_without_network(panel,tmp_path):
    import asyncio
    from investment_agent.agent import ResearchAgent
    from investment_agent.config import Settings
    store=DatasetStore(tmp_path)
    meta=store.save(panel,source="synthetic-test",adjustment="raw",synthetic=True)
    agent=ResearchAgent(Settings(root=tmp_path,enable_choice=False,enable_miaoxiang=False,enable_public=False))
    result=asyncio.run(agent.dispatch("analyze_factors",{"dataset_id":meta["id"],"lookback":5}))
    assert result["ok"] and result["synthetic"]
    report=QuantLab(tmp_path).daily(meta["id"])
    assert "合成示例" in report["data"]["report"]
    assert agent.service.calls==0


def test_choice_ohlcv_is_ready_without_real_login(tmp_path):
    from types import SimpleNamespace
    from investment_agent.choice import ChoiceProvider
    from investment_agent.config import Settings
    calls=[]
    class SDK:
        def start(self,*args,**kwargs): return SimpleNamespace(ErrorCode=0)
        def stop(self): calls.append("stop")
        def csd(self,code,indicators,start,end,options):
            calls.append((code,indicators,options))
            return SimpleNamespace(ErrorCode=0,Indicators=indicators.split(","),Dates=["2025-01-02","2025-01-03"],
                Data={code:[[10,11],[11,12],[9,10],[10,11],[10000,20000],[100000,220000]]})
    provider=ChoiceProvider(Settings(root=tmp_path,enable_choice=True),loader=lambda:SDK())
    result=provider.request("ohlcv","000001.SZ","2025-01-01","2025-04-01")
    assert result.ok and result.data["rows"][1]["close"]==11
    assert result.data["rows"][0]["volume"]==10000
    assert "AdjustFlag=1" in calls[0][2] and calls[-1]=="stop"
    provider.settings.enable_choice=False
    with pytest.raises(DataError,match="关闭"): provider.request("ohlcv","000001.SZ","2025-01-01","2025-01-10")


def test_wrong_vendor_symbol_and_weekend_data_are_rejected(panel):
    frame=panel.copy(); frame["code"]="sh.600519"
    with pytest.raises(DataError,match="冲突"): normalize(frame)
    frame=panel.iloc[:1].copy(); frame["date"]="2025-01-04"
    with pytest.raises(DataError,match="周末"): normalize(frame)


def test_mcp_native_horizontal_table_units_and_missing_ohlcv():
    from investment_agent.quant_data import extract_rows
    columns=["贵州茅台(600519.SH)","2025-12-31(日)","2025-12-30(日)"]
    data={"data":[{"columns":columns,"items":[["开盘价","10元","11元"],["最高价","12元","12元"],
        ["最低价","9元","10元"],["收盘价","11元","11元"],["成交量(手)","1万手","2万手"],["成交额","1000万元","2000万元"]]}]}
    frame=normalize(pd.DataFrame(extract_rows(data,"600519.SH")))
    assert frame.iloc[-1].volume==1e6 and frame.iloc[-1].amount==1e7
    data["data"][0]["items"]=[r for r in data["data"][0]["items"] if r[0]!="最高价"]
    with pytest.raises(DataError,match="OHLCV"): extract_rows(data,"600519.SH")


def test_pit_rejects_naive_timestamp(panel):
    frame=panel.copy(); frame["available_at"]="2025-01-01 09:00:00"
    with pytest.raises(DataError,match="时区"): normalize(frame)
