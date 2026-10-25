import asyncio
from dataclasses import replace
from unittest.mock import patch
import pytest
from investment_agent.financial_model import statement_analysis,fcff_dcf,DCFAssumptions,dcf_scenarios,research_financials
from investment_agent.common import DataError
from investment_agent.config import Settings

def assumptions():
    return DCFAssumptions(.1,.2,.25,.03,.05,.1,.1,.02,basis='synthetic sensitivity test only')

def test_dupont_consistent_ownership_and_average_balances():
    f={'periods':[{'period':'2025-12-31','values':{'revenue':200,'profit_total':30,'income_tax':10,'assets':120,'equity_total':60,'operating_cashflow':25,'capex_cash':15}},
                  {'period':'2024-12-31','values':{'assets':80,'equity_total':40}}]}
    r=statement_analysis(f)['rows'][0]
    assert r['consolidated_net_profit']==20
    assert r['dupont_roe']==pytest.approx(.4)
    assert r['net_margin']*r['asset_turnover']*r['equity_multiplier']==r['dupont_roe']
    assert r['cash_after_capex']==10
    assert statement_analysis({'periods':[f['periods'][0]]})['rows'][0]['dupont_roe'] is None

def test_dcf_cashflow_identity_and_terminal_discount():
    r=fcff_dcf(100,'2025-12-31',assumptions())
    first=r['forecasts'][0]
    assert first['revenue']==pytest.approx(110)
    assert first['fcff']==pytest.approx(22*.75+3.3-5.5-1)
    assert r['enterprise_value']==pytest.approx(sum(x['present_value'] for x in r['forecasts'])+r['terminal_fcff']/(.1-.02)/(1.1**5))
    assert 'price_per_share' not in r

@pytest.mark.parametrize('changes',[{'wacc':.02,'terminal_growth':.02},{'basis':''},{'tax_rate':float('nan')},{'capex_ratio':2}])
def test_invalid_assumptions_rejected(changes):
    with pytest.raises(DataError): fcff_dcf(100,'2025-12-31',replace(assumptions(),**changes))

def test_midyear_not_annualized_and_missing_not_zero():
    with pytest.raises(DataError): fcff_dcf(100,'2025-06-30',assumptions())
    with pytest.raises(DataError): fcff_dcf(None,'2025-12-31',assumptions())
    with pytest.raises(DataError): fcff_dcf(100,'2025-12-31',replace(assumptions(),capex_ratio=.9))

def test_discount_rate_sensitivity():
    low=fcff_dcf(100,'2025-12-31',replace(assumptions(),wacc=.08))
    high=fcff_dcf(100,'2025-12-31',replace(assumptions(),wacc=.12))
    assert low['enterprise_value']>high['enterprise_value']

def test_financial_tool_registered():
    from investment_agent.agent import TOOLS
    assert any(t['function']['name']=='financial_model' for t in TOOLS)

def test_disabled_source_blocks_network(tmp_path):
    with pytest.raises(DataError,match='公开数据'):
        asyncio.run(research_financials(Settings(root=tmp_path,enable_public=False),'002156','通富微电'))

def test_annual_pdf_parser_does_not_treat_half_year_as_full_year():
    from investment_agent.ashare_metrics import reconcile_report
    text='样本公司 000001 2025年年度报告\n'+'目录 '*100+'主要会计数据\n单位：元\n营业收入 200.00 100.00\n'
    metrics={'name':'样本公司','symbol':'000001.SZ','periods':[]}
    assert reconcile_report(text,metrics,'2025-12-31')['checks'][0]['current_cny']==200
    assert not reconcile_report(text.replace('年度报告','半年度报告'),metrics,'2025-12-31')['checks']
