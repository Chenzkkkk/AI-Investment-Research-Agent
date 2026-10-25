"""Independent small-number oracles and adversarial accounting cases."""
import pytest
from investment_agent import professional_models as m
from investment_agent.common import DataError


def test_linked_statements_hand_worked_example():
    base=dict(revenue=100,cash=20,receivables=10,inventory=10,payables=10,ppe=60,other_assets=0,debt=30,other_liabilities=0,equity=60)
    driver=dict(growth=.1,gross_margin=.4,opex_ratio=.1,tax_rate=.25,dso=36.5,dio=365/6,dpo=365/6,
                capex_ratio=.1,depreciation_ratio=.1,interest_rate=.1,payout=.5,new_debt=0,debt_repayment=0,equity_issue=0,basis='synthetic test assumptions')
    result=m.three_statement(base,[driver]); r=result['rows'][0]
    assert result['valid']
    for key,value in {'revenue':110,'net_income':18,'cash':23,'equity':69,'cfo':23,'fcff':14.25,'fcfe':12}.items():
        assert r[key]==pytest.approx(value)
    assert r['balance_residual']==pytest.approx(0,abs=1e-9)
    assert r['cash_roll_residual']==pytest.approx(0,abs=1e-9)
    stressed=m.three_statement(base,[{**driver,'capex_ratio':.9}])['rows'][0]
    assert stressed['financing_gap']>0 and stressed['cash']<0 and stressed['debt']==30
    with pytest.raises(DataError,match='不平衡'): m.three_statement({**base,'equity':61},[driver])


def test_cashflow_valuation_and_equity_bridge_are_not_mixed():
    # Zero-growth cash flows of 10 forever discounted at 10% are worth 100.
    assert m.discounted_equity([10,10],.1,0)['equity_value']==pytest.approx(100)
    bridge=m.equity_bridge(dict(enterprise_value=100,debt=30,cash=10,minority_interest=5,nonoperating_assets=5,shares=10))
    assert bridge['equity_value']==80 and bridge['value_per_share']==8
    assert m.residual_income(100,[10,10],[10,10],.1)['equity_value']==pytest.approx(100)
    for k,g,flows in [(.02,.03,[10]),(.1,.02,[-1])]:
        with pytest.raises(DataError): m.discounted_equity(flows,k,g)


def test_annual_quarter_ttm_and_vintages():
    rows=[{'period':'2024-12-31','published_at':'2025-03-01','values':{'revenue':100}},
          {'period':'2025-03-31','published_at':'2025-04-01','values':{'revenue':30}},
          {'period':'2024-06-30','published_at':'2024-08-01','values':{'revenue':40}},
          {'period':'2025-06-30','published_at':'2025-08-01','values':{'revenue':70}},
          {'period':'2025-06-30','published_at':'2025-09-01','values':{'revenue':72}},
          {'period':'2025-06-30','published_at':None,'values':{'revenue':999}}]
    result=m.normalize_periods(rows,'2025-08-20'); r=result['rows'][0]
    assert r['single_quarter']['revenue']==40 and r['ttm']['revenue']==130
    assert m.normalize_periods(rows,'2025-09-20')['rows'][0]['ttm']['revenue']==132
    assert len(result['excluded'])==2
    assert r['single_quarter']['capex_cash'] is None


def test_quality_flags_and_missing_values():
    result=m.quality_analysis([{'period':'2025-12-31','values':{'net_income':10,'operating_cashflow':-5,'government_grants':4,'profit_total':12}}])
    assert any('补助' in f['factor'] for f in result['findings'])
    assert any('现金流为负' in f['factor'] for f in result['findings'])
    assert result['rows'][0]['interest_coverage'] is None


def test_roic_hand_calculation():
    r=m.capital_analysis(dict(ebit=20,tax_rate=.25,debt_begin=30,debt_end=30,equity_begin=80,equity_end=100,cash_begin=10,cash_end=10,wacc=.1))
    assert r['average_invested_capital']==110
    assert r['roic']==pytest.approx(15/110) and r['economic_profit']==pytest.approx(4)


def test_peer_exclusions_and_portfolio_initial_loss():
    peers=[dict(pe=p,comparable=True,basis='same business and period',source_url='https://example.com') for p in (10,20,30)]
    r=m.peer_valuation(peers,{'earnings':5})
    assert r['valuations']['pe']['implied_value']==100
    assert not m.peer_valuation(peers,{'earnings':-1})['valuations']
    r=m.portfolio_diagnostics([[-.2], [0], [0]],[1])
    assert r['max_drawdown']==pytest.approx(.2) and r['weight_hhi']==1
    with pytest.raises(DataError): m.portfolio_diagnostics([[.1],[.2],[.3]],[.8])


def test_pdf_numeric_parser_never_turns_missing_into_zero():
    from investment_agent.financial_documents import parse_amount
    assert parse_amount('(1,234.50)')==-1234.5
    assert parse_amount('—') is None and parse_amount('不适用') is None


def test_capm_regression_and_scenario_oracles():
    r=m.cost_of_capital(.03,1.2,.05,.04,.25,.6)
    assert r['cost_of_equity']==pytest.approx(.09) and r['wacc']==pytest.approx(.066)
    r=m.regression_analysis([[1],[2],[3],[4],[5]],[5,8,11,14,17])
    assert r['intercept']==pytest.approx(2) and r['coefficients'][0]==pytest.approx(3) and r['r_squared']==pytest.approx(1)
    with pytest.raises(DataError): m.regression_analysis([[1],[1],[1],[1]],[1,2,3,4])
    r=m.exposure_scenarios({'export_net':100,'raw_material':-50},[{'name':'test','changes':{'export_net':-.1,'raw_material':.2},'basis':'synthetic shock'}])
    assert r['scenarios'][0]['estimated_pnl']==-20
