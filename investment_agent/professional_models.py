"""Versioned, deterministic research models. Money is CNY; rates are decimals.

Public methodology, independently implemented. No vendor forecasts or missing-to-zero
conversion. Every forward input is an explicit scenario, never a reported fact.
"""
from __future__ import annotations

import math
from statistics import median
import numpy as np
from .common import DataError

VERSION = '1.0.0'


def finite(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise DataError('MODEL_INPUT', f'{name}需要有限数值；缺失不能填零。')
    return float(value)


def required(data, names):
    return {k: finite(data.get(k), k) for k in names}


def rate(value, name, low=-1, high=1):
    x = finite(value, name)
    if not low <= x <= high:
        raise DataError('MODEL_RATE', f'{name}须以小数表示，范围{low}至{high}。')
    return x


def normalize_periods(periods, as_of):
    """Latest visible vintage per period; quarter/TTM flows never annualised.

    Input contracts require original consolidated CNY cumulative flows. Unknown
    publication dates and incompatible scopes are excluded, not silently joined.
    """
    flows = ('revenue', 'profit_parent', 'net_income', 'operating_cashflow', 'capex_cash')
    selected, excluded = {}, []
    for row in periods:
        p, published = row.get('period', ''), row.get('published_at', '')
        if not published or not p or published[:10] > as_of or p > as_of:
            excluded.append({'period': p, 'reason': '发布时间未知或晚于截止日'})
            continue
        if row.get('currency', 'CNY') != 'CNY' or row.get('scope', 'consolidated') != 'consolidated':
            excluded.append({'period': p, 'reason': '货币或合并范围不兼容'})
            continue
        old = selected.get(p)
        if old is None or published > old['published_at']:
            selected[p] = row
    output = []
    for p, row in sorted(selected.items(), reverse=True):
        year, md = int(p[:4]), p[5:]
        if md not in ('03-31', '06-30', '09-30', '12-31'):
            excluded.append({'period': p, 'reason': '暂不支持非日历财年'})
            continue
        prev_md = {'06-30':'03-31', '09-30':'06-30', '12-31':'09-30'}.get(md)
        prev = selected.get(f'{year}-{prev_md}', {}).get('values', {})
        annual = selected.get(f'{year-1}-12-31', {}).get('values', {})
        last = selected.get(f'{year-1}-{md}', {}).get('values', {})
        values = row['values']; quarter, ttm = {}, {}
        for k in flows:
            v = values.get(k)
            valid = lambda x: isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)
            quarter[k] = v if md == '03-31' and valid(v) else v-prev[k] if valid(v) and valid(prev.get(k)) else None
            ttm[k] = v if md == '12-31' and valid(v) else v+annual[k]-last[k] if all(valid(x) for x in (v,annual.get(k),last.get(k))) else None
        output.append({**row, 'single_quarter': quarter, 'ttm': ttm})
    return {'rows': output, 'excluded': excluded, 'version': VERSION,
            'limits': ['仅处理已提供的可见版本；现时接口不保证还原历史修订前数值。']}


def quality_analysis(periods):
    """Screening warnings are investigation triggers, not fraud accusations."""
    findings, rows = [], []
    for row in periods:
        v = row['values']; p = row['period']
        n = v.get('net_income')
        if n is None and v.get('profit_total') is not None and v.get('income_tax') is not None:
            n = v['profit_total'] - v['income_tax']
        def ratio(a, b):
            return a/b if a is not None and b is not None and b > 0 else None
        cfo = v.get('operating_cashflow')
        r = {'period': p, 'cash_conversion': ratio(cfo,n),
             'parent_nonrecurring_share': ratio(v.get('nonrecurring_profit'),v.get('profit_parent')),
             'subsidy_to_total_profit':ratio(v.get('government_grants'),v.get('profit_total')),
             'receivables_to_revenue':ratio(v.get('receivables'),v.get('revenue')),
             'inventory_to_revenue':ratio(v.get('inventory'),v.get('revenue')),
             'interest_coverage':ratio(v.get('ebit'),v.get('interest_expense')),
             'sources':row.get('source_tables', [])}
        for key, threshold, label in [('cash_conversion',.8,'盈利现金转换偏弱'),
                                      ('subsidy_to_total_profit',.2,'补助对利润影响较大')]:
            x = r[key]
            if x is not None and (x < threshold if key == 'cash_conversion' else x > threshold):
                findings.append({'period':p,'factor':label,'value':x,'threshold':threshold,'status':'需核验附注与行业季节性'})
        if cfo is not None and cfo < 0 and n is not None and n > 0:
            findings.append({'period':p,'factor':'盈利为正但经营现金流为负','status':'现金质量预警'})
        rows.append(r)
    return {'rows':rows,'findings':findings,'version':VERSION,
            'limits':['阈值为调查触发值，不是会计违规或投资评级。', '缺失附注字段保持未知。']}


def capital_analysis(data):
    """NOPAT / average invested capital. Explicit cash/debt scope required."""
    d=required(data, ['ebit','tax_rate','debt_begin','debt_end','equity_begin','equity_end','cash_begin','cash_end','wacc'])
    rate(d['tax_rate'],'tax_rate',0,1); rate(d['wacc'],'wacc',0,1)
    capital=((d['debt_begin']+d['equity_begin']-d['cash_begin'])+(d['debt_end']+d['equity_end']-d['cash_end']))/2
    if capital<=0: raise DataError('ROIC_NOT_APPLICABLE','平均投入资本非正，ROIC不适用。')
    nopat=d['ebit']*(1-d['tax_rate']); roic=nopat/capital
    return {'nopat':nopat,'average_invested_capital':capital,'roic':roic,
            'roic_minus_wacc':roic-d['wacc'],'economic_profit':nopat-capital*d['wacc'],
            'limits':['需使用一致的年度、经营资产和有息负债口径；金融企业不适用此简化定义。']}


def three_statement(base, drivers):
    """Annual linked three-statement scenarios, explicit debt financing.

    This bounded model uses non-cash operating NWC and fixed assets. Other assets
    and liabilities remain explicit constants. No hidden balancing plug.
    """
    b=required(base,['revenue','cash','receivables','inventory','payables','ppe','other_assets','debt','other_liabilities','equity'])
    if b['revenue']<=0: raise DataError('BASE_REVENUE','基期年度收入须为正。')
    assets=b['cash']+b['receivables']+b['inventory']+b['ppe']+b['other_assets']
    residual=assets-b['payables']-b['debt']-b['other_liabilities']-b['equity']
    tol=max(.01,abs(assets)*1e-8)
    if abs(residual)>tol: raise DataError('BASE_BALANCE','期初资产负债表不平衡，不能自动补平。')
    if not 1<=len(drivers)<=10: raise DataError('FORECAST_YEARS','预测期须为1至10年。')
    rows=[]; prev=b.copy()
    for year, raw in enumerate(drivers,1):
        if not raw.get('basis'): raise DataError('ASSUMPTION_BASIS','每期预测须提供假设依据。')
        d=required(raw,['growth','gross_margin','opex_ratio','tax_rate','dso','dio','dpo','capex_ratio','depreciation_ratio','interest_rate','payout','new_debt','debt_repayment','equity_issue'])
        for k in ('gross_margin','opex_ratio','tax_rate','capex_ratio','depreciation_ratio','interest_rate','payout'): rate(d[k],k,0,1)
        rate(d['growth'],'growth',-.99,3)
        if any(d[k]<0 for k in ('dso','dio','dpo','new_debt','debt_repayment','equity_issue')): raise DataError('DRIVER_RANGE','周转天数与融资金额不可为负。')
        revenue=prev['revenue']*(1+d['growth']); cogs=revenue*(1-d['gross_margin'])
        capex=revenue*d['capex_ratio']; da=prev['ppe']*d['depreciation_ratio']
        debt=prev['debt']+d['new_debt']-d['debt_repayment']
        if debt<0: raise DataError('DEBT_REPAYMENT','偿债额超过可偿还债务。')
        interest=(prev['debt']+debt)/2*d['interest_rate']
        ebit=revenue-cogs-revenue*d['opex_ratio']-da
        pretax=ebit-interest; tax=max(pretax,0)*d['tax_rate']; income=pretax-tax
        ar=revenue*d['dso']/365; inv=cogs*d['dio']/365; ap=cogs*d['dpo']/365
        delta_nwc=(ar+inv-ap)-(prev['receivables']+prev['inventory']-prev['payables'])
        cfo=income+da-delta_nwc; dividends=max(income,0)*d['payout']
        cfi=-capex; cff=d['new_debt']-d['debt_repayment']+d['equity_issue']-dividends
        cash=prev['cash']+cfo+cfi+cff; equity=prev['equity']+income-dividends+d['equity_issue']
        ppe=prev['ppe']+capex-da
        total_assets=cash+ar+inv+ppe+b['other_assets']
        balance=total_assets-ap-debt-b['other_liabilities']-equity
        # FCFF cash tax is on unlevered EBIT; no automatic tax-loss credit.
        fcff=ebit-max(ebit,0)*d['tax_rate']+da-capex-delta_nwc
        fcfe=cfo-capex+d['new_debt']-d['debt_repayment']
        rows.append({'year':year,'revenue':revenue,'ebit':ebit,'interest':interest,'tax':tax,'net_income':income,
            'cash':cash,'receivables':ar,'inventory':inv,'payables':ap,'ppe':ppe,'debt':debt,'equity':equity,
            'cfo':cfo,'cfi':cfi,'cff':cff,'dividends':dividends,'fcff':fcff,'fcfe':fcfe,
            'financing_gap':max(-cash,0),'balance_residual':balance,
            'cash_roll_residual':cash-prev['cash']-cfo-cfi-cff,
            'equity_roll_residual':equity-prev['equity']-income+dividends-d['equity_issue'],
            'assumptions':raw})
        prev={**b,**rows[-1]}
    return {'model':'linked-three-statement','version':VERSION,'rows':rows,
            'valid':all(abs(r['balance_residual'])<=max(.01,abs(r['equity'])*1e-8) for r in rows),
            'limits':['非金融企业年度情景；其他资产负债保持不变。', '不自动融资补平，负现金显示资金缺口。',
                      '不自动确认亏损递延所得税利益；费用率不含折旧，利息使用平均有息债务。']}


def equity_bridge(data):
    d=required(data,['enterprise_value','debt','cash','minority_interest','nonoperating_assets','shares'])
    if d['shares']<=0: raise DataError('SHARES','需正的估值日股数，不可混用加权平均EPS股数。')
    equity=d['enterprise_value']-d['debt']+d['cash']-d['minority_interest']+d['nonoperating_assets']
    return {'equity_value':equity,'value_per_share':equity/d['shares'], 'inputs':d,
            'limits':['现金须为可分配非经营现金；租赁与少数股东口径需与企业价值一致。']}


def discounted_equity(flows, cost_of_equity, terminal_growth, method='FCFE'):
    k=rate(cost_of_equity,'cost_of_equity',.001,1); g=rate(terminal_growth,'terminal_growth',-.5,.2)
    if k<=g or not flows: raise DataError('TERMINAL','折现率须高于长期增长率且预测不能为空。')
    f=[finite(x,'flow') for x in flows]
    if f[-1]<=0: raise DataError('TERMINAL_FLOW','终期现金流非正，永续增长模型不适用。')
    pv=[x/(1+k)**(i+1) for i,x in enumerate(f)]; terminal=f[-1]*(1+g)/(k-g)/(1+k)**len(f)
    return {'method':method,'equity_value':sum(pv)+terminal,'present_values':pv,'terminal_pv':terminal,
            'terminal_share':terminal/(sum(pv)+terminal) if sum(pv)+terminal>0 else None,
            'limits':['终期须已达到稳态；不得将不稳定末年现金流直接认定为永续值。']}


def residual_income(book_equity, earnings, dividends, cost_of_equity):
    book=finite(book_equity,'book_equity'); k=rate(cost_of_equity,'cost_of_equity',.001,1)
    if book<=0 or not earnings or len(earnings)!=len(dividends): raise DataError('RI_INPUT','剩余收益需要正净资产和等长利润分红预测。')
    value=book; rows=[]
    for i,(e,d) in enumerate(zip(earnings,dividends),1):
        e=finite(e,'earnings'); d=finite(d,'dividends'); residual=e-k*book
        value+=residual/(1+k)**i; book+=e-d
        rows.append({'year':i,'residual_income':residual,'closing_book_equity':book})
    return {'equity_value':value,'rows':rows,'terminal_assumption':'预测期后剩余收益为零；其他综合收益及增发需另行调整清洁盈余关系。'}


def peer_valuation(peers, subject):
    output={}; rejected=[]
    for metric in ('pe','pb','ps','ev_ebitda'):
        values=[]
        for peer in peers:
            x=peer.get(metric)
            if peer.get('comparable') is not True or not peer.get('basis') or not peer.get('source_url'):
                continue
            if isinstance(x,(int,float)) and math.isfinite(x) and x>0: values.append(x)
        denominator=subject.get({'pe':'earnings','pb':'book_equity','ps':'revenue','ev_ebitda':'ebitda'}[metric])
        if len(values)<3 or denominator is None or denominator<=0:
            rejected.append({'metric':metric,'reason':'至少三个经业务核验的可比样本及正的同口径分母'})
            continue
        output[metric]={'sample_size':len(values),'median':median(values),'implied_value':median(values)*denominator,
                        'value_type':'enterprise' if metric=='ev_ebitda' else 'equity'}
    return {'valuations':output,'unavailable':rejected,'limits':['行业、时点、币种、会计和年度/TTM口径需一致；PS不反映盈利能力。']}


def portfolio_diagnostics(returns, weights, benchmark=None, periods_per_year=252):
    w=np.asarray(weights,dtype=float); r=np.asarray(returns,dtype=float)
    if r.ndim!=2 or r.shape[1]!=len(w) or r.shape[0]<3 or not np.isfinite(r).all() or not np.isfinite(w).all() or (w<0).any() or abs(w.sum()-1)>1e-8 or (r<=-1).any():
        raise DataError('PORTFOLIO_INPUT','需对齐的收益率矩阵、至少3期和合计为1的非负权重。')
    p=r@w; nav=np.cumprod(1+p); peak=np.maximum.accumulate(np.r_[1.,nav])[1:]
    vol=float(np.std(p,ddof=1)*np.sqrt(periods_per_year))
    out={'total_return':float(nav[-1]-1),'annual_volatility':vol,'max_drawdown':float(np.max(1-nav/peak)),
         'weight_hhi':float(w@w),'largest_weight':float(w.max()),'observations':len(p),
         'correlation':np.nan_to_num(np.atleast_2d(np.corrcoef(r,rowvar=False)),nan=0).tolist(),
         'limits':['固定权重逐期再平衡的历史诊断，未计费用；不是可执行回测。']}
    if benchmark is not None:
        b=np.asarray(benchmark,dtype=float)
        if b.shape!=p.shape or not np.isfinite(b).all(): raise DataError('BENCHMARK','基准必须按同日期对齐。')
        out['tracking_error']=float(np.std(p-b,ddof=1)*np.sqrt(periods_per_year))
        out['active_return_sum']=float(np.sum(p-b))
    return out


def cost_of_capital(risk_free,beta,equity_risk_premium,cost_of_debt,tax_rate,equity_weight):
    rf=rate(risk_free,'risk_free',-.1,.5); beta=finite(beta,'beta')
    premium=rate(equity_risk_premium,'equity_risk_premium',0,.5)
    kd=rate(cost_of_debt,'cost_of_debt',0,1); tax=rate(tax_rate,'tax_rate',0,1); w=rate(equity_weight,'equity_weight',0,1)
    ke=rf+beta*premium
    return {'cost_of_equity':ke,'wacc':w*ke+(1-w)*kd*(1-tax),
            'limits':['资本权重使用市场价值；无风险利率、风险溢价、现金流币种和期限须一致。Beta为估计，不是稳定常量。']}


def regression_analysis(features, target, feature_names=None):
    """OLS with intercept; descriptive in-sample diagnostics, no causal claim."""
    x=np.asarray(features,dtype=float); y=np.asarray(target,dtype=float)
    if x.ndim!=2 or y.ndim!=1 or len(x)!=len(y) or len(y)<=x.shape[1]+2 or not np.isfinite(x).all() or not np.isfinite(y).all():
        raise DataError('REGRESSION_INPUT','需完整对齐有限值，观测数大于变量数+2；不自动填补缺失。')
    design=np.column_stack([np.ones(len(y)),x])
    if np.linalg.matrix_rank(design)<design.shape[1]: raise DataError('REGRESSION_RANK','完全共线或常数特征，不输出不稳定估计。')
    coefficients=np.linalg.lstsq(design,y,rcond=None)[0]; residual=y-design@coefficients
    total=np.sum((y-y.mean())**2)
    if total==0: raise DataError('REGRESSION_TARGET','目标变量没有变动，拟合优度不适用。')
    return {'intercept':float(coefficients[0]),'coefficients':coefficients[1:].tolist(),
            'features':feature_names or [f'x{i+1}' for i in range(x.shape[1])],
            'r_squared':float(1-np.sum(residual**2)/total),'observations':len(y),
            'limits':['样本内OLS描述，不证明因果或投资预测能力。时间序列需检查平稳性、滞后及样本外，当前不自动提供显著性结论。']}


def exposure_scenarios(exposures, shocks):
    """First-order P&L approximation on explicitly disclosed net exposures."""
    rows=[]
    for scenario in shocks:
        if not scenario.get('basis'): raise DataError('SCENARIO_BASIS','压力情景须说明依据。')
        contributions={}
        for factor,amount in exposures.items():
            if factor not in scenario['changes']: raise DataError('SHOCK_MISSING','情景缺少敞口对应冲击。')
            contributions[factor]=finite(amount,factor)*rate(scenario['changes'][factor],factor,-1,3)
        rows.append({'name':scenario.get('name','情景'),'estimated_pnl':sum(contributions.values()),'contributions':contributions,'basis':scenario['basis']})
    return {'scenarios':rows,'limits':['净敞口×冲击的一阶近似；忽略非线性、经营调整、相关性与套保基差。名义本金不能直接当净敞口。']}
