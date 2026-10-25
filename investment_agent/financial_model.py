"""Deterministic statement analysis, DuPont and explicit-assumption FCFF DCF.

Amounts are CNY, rates are decimal. Not a linked three-statement forecast.
No inferred zeroes, half-year annualisation or LLM arithmetic.
"""
from dataclasses import dataclass, asdict, replace
from datetime import date,timedelta
import math
from .common import DataError, now, symbol

MODEL_REFERENCES={
    'FCFF DCF':'https://pages.stern.nyu.edu/~adamodar/New_Home_Page/littlebook/cashflows.htm',
    'DuPont':'https://corporatefinanceinstitute.com/resources/accounting/dupont-analysis/'}

def value(x):
    return float(x) if isinstance(x,(int,float)) and not isinstance(x,bool) and math.isfinite(x) else None

def ratio(a,b):
    a,b=value(a),value(b)
    return a/b if a is not None and b is not None and b>0 else None

def statement_analysis(financials):
    periods=financials.get('periods',[])
    rows=[]
    for p in periods:
        v=p['values']; end=p['period']
        prior=next((q['values'] for q in periods if q['period']==str(int(end[:4])-1)+'-12-31'),{})
        # Consolidated profit and consolidated equity avoid mixing ownership scopes.
        profit=value(v.get('profit_total')); tax=value(v.get('income_tax'))
        net=profit-tax if profit is not None and tax is not None else None
        avg_assets=(v['assets']+prior['assets'])/2 if all(value(x) is not None for x in (v.get('assets'),prior.get('assets'))) else None
        avg_equity=(v['equity_total']+prior['equity_total'])/2 if all(value(x) is not None for x in (v.get('equity_total'),prior.get('equity_total'))) else None
        margin=ratio(net,v.get('revenue')); turnover=ratio(v.get('revenue'),avg_assets)
        leverage=ratio(avg_assets,avg_equity)
        dupont=margin*turnover*leverage if all(x is not None for x in (margin,turnover,leverage)) else None
        cfo=value(v.get('operating_cashflow')); capex=value(v.get('capex_cash'))
        rows.append({'period':end,'published_at':p.get('published_at'),'values':v,
            'consolidated_net_profit':net,'net_margin':margin,'asset_turnover':turnover,
            'equity_multiplier':leverage,'dupont_roe':dupont,'cash_conversion':ratio(cfo,net),
            'cash_after_capex':cfo-capex if cfo is not None and capex is not None else None,
            'debt_to_assets':ratio(v.get('liabilities'),v.get('assets')),
            'sources':p.get('source_tables',[]),
            'missing_for_dupont':[name for name,x in [('利润总额',profit),('所得税费用',tax),('营业收入',v.get('revenue')),('期初期末平均资产',avg_assets),('期初期末平均权益',avg_equity)] if value(x) is None],
            'limits':['ROE为合并净利润/期初期末平均合并权益，不等于公告归母加权ROE。',
                '中期比率为年初至报告期末累计口径，未年化。',
                '经营现金流减现金资本开支仅为现金余额指标，不冒充FCFF或FCFE。']})
    return {'model':'statement-analysis+DuPont','rows':rows,'references':MODEL_REFERENCES}

@dataclass(frozen=True)
class DCFAssumptions:
    revenue_growth: float
    operating_margin: float
    tax_rate: float
    depreciation_ratio: float
    capex_ratio: float
    working_capital_ratio: float
    wacc: float
    terminal_growth: float
    years: int=5
    basis: str=''

    def validate(self):
        bounds={'revenue_growth':(-.5,1.),'operating_margin':(-.5,.8),'tax_rate':(0.,.6),
            'depreciation_ratio':(0.,.5),'capex_ratio':(0.,1.),'working_capital_ratio':(0.,1.),
            'wacc':(.01,.5),'terminal_growth':(-.02,.05)}
        for k,(low,high) in bounds.items():
            v=value(getattr(self,k))
            if v is None or not low<=v<=high: raise DataError('DCF_ASSUMPTION',f'{k}超出模型允许范围。')
        if not isinstance(self.years,int) or not 3<=self.years<=10: raise DataError('DCF_YEARS','预测期须为3至10年。')
        if self.wacc<=self.terminal_growth: raise DataError('DCF_TERMINAL','WACC必须高于永续增长率。')
        if not self.basis.strip(): raise DataError('DCF_BASIS','必须说明假设依据；不得把假设标为已披露事实。')

def fcff_dcf(base_revenue,base_period,assumptions):
    a=assumptions if isinstance(assumptions,DCFAssumptions) else DCFAssumptions(**assumptions)
    a.validate()
    if value(base_revenue) is None or base_revenue<=0 or not base_period.endswith('-12-31'):
        raise DataError('DCF_BASE','DCF须使用已披露正营收的完整年度；不可机械年化中报。')
    def forecast(previous,growth,year):
        revenue=previous*(1+growth); ebit=revenue*a.operating_margin
        tax=max(ebit,0)*a.tax_rate  # No unverified immediate cash tax credit on losses.
        da=revenue*a.depreciation_ratio; capex=revenue*a.capex_ratio
        nwc_change=(revenue-previous)*a.working_capital_ratio
        fcff=ebit-tax+da-capex-nwc_change
        return {'year':year,'revenue':revenue,'ebit':ebit,'cash_tax':tax,'depreciation':da,
            'capex':capex,'delta_nwc':nwc_change,'fcff':fcff,'present_value':fcff/(1+a.wacc)**year}
    rows=[]; revenue=base_revenue
    for year in range(1,a.years+1):
        row=forecast(revenue,a.revenue_growth,year); rows.append(row); revenue=row['revenue']
    terminal=forecast(revenue,a.terminal_growth,a.years+1)
    if terminal['fcff']<=0:
        raise DataError('DCF_NO_STABLE_CASH','永续期自由现金流非正，不能用正向稳定增长估值；需重设商业假设。')
    terminal_value=terminal['fcff']/(a.wacc-a.terminal_growth)
    terminal_pv=terminal_value/(1+a.wacc)**a.years
    ev=sum(r['present_value'] for r in rows)+terminal_pv
    return {'model':'FCFF-DCF','base_period':base_period,'base_revenue':base_revenue,
        'assumptions':asdict(a),'forecasts':rows,'terminal_fcff':terminal['fcff'],
        'enterprise_value':ev,'terminal_pv_share':terminal_pv/ev if ev>0 else None,
        'formula':'FCFF=EBIT×(1-tax)+D&A-capex-ΔNWC; EV=PV(FCFF)+PV(terminal FCFF/(WACC-g))',
        'references':MODEL_REFERENCES,
        'limits':['仅为显式假设下的经营企业价值，不是股权市值或每股目标价。',
            '未核验净债务、少数股东权益、非经营资产、稀释股数前不计算每股价值。',
            '假设须核对长期投入与产能需求；固定比例并非完整三表联动预测。',
            '本模型不适用于银行保险；亏损转型企业需先建立可验证的正常化盈利路径。']}

def dcf_scenarios(base_revenue,base_period,assumptions):
    a=DCFAssumptions(**assumptions); a.validate()
    variants={'审慎':replace(a,revenue_growth=max(-.5,a.revenue_growth-.03),wacc=min(.5,a.wacc+.02)),
              '基准':a,'积极':replace(a,revenue_growth=min(1.,a.revenue_growth+.03),wacc=max(a.terminal_growth+.01,a.wacc-.02))}
    scenarios={}
    for label,variant in variants.items():
        try: scenarios[label]=fcff_dcf(base_revenue,base_period,variant)
        except DataError as exc: scenarios[label]={'error':exc.code,'message':str(exc)}
    sensitivity=[]
    for wacc in (a.wacc-.02,a.wacc,a.wacc+.02):
        for growth in (a.terminal_growth-.01,a.terminal_growth,a.terminal_growth+.01):
            try: v=fcff_dcf(base_revenue,base_period,replace(a,wacc=wacc,terminal_growth=growth))['enterprise_value']
            except DataError: v=None
            sensitivity.append({'wacc':wacc,'terminal_growth':growth,'enterprise_value':v})
    return {'scenarios':scenarios,'sensitivity':sensitivity,'scenario_note':'情景为增长±3个百分点、WACC∓2个百分点的机械压力测试，不是概率预测。'}

async def research_financials(settings,code,name,assumptions=None):
    import re
    if not settings.enable_public: raise DataError('PUBLIC_DISABLED','请先启用公开数据源。')
    if not re.fullmatch(r'[\u4e00-\u9fffA-Za-z*]{2,15}',name): raise DataError('STOCK_NAME','证券简称格式不正确。')
    from .sector_screen import public_financials,normalize_financials
    from .service import DataService
    from .ashare_sources import official_disclosures,fetch_document
    from .ashare_metrics import reconcile_report
    code=symbol(code); cutoff=now()[:10]
    asset={'code':code,'name':name}
    records,requests=await public_financials(DataService(settings),[asset],cutoff)
    for record in records.get(code,[]):
        actual=record['row'].get('SECURITY_NAME_ABBR')
        if actual and actual.replace(' ','')!=name.replace(' ',''):
            raise DataError('ISSUER_MISMATCH','证券代码与公开财报简称不一致，请核对后重试。')
    financials=normalize_financials(records.get(code,[]),asset,cutoff)
    start=(date.fromisoformat(cutoff)-timedelta(days=800)).isoformat()
    official=await official_disclosures([asset],start,cutoff,report_only=True)
    # Retry explicit report documents when the structured route is empty. Never infer non-disclosure.
    fallbacks=[]
    needs_pdf=not financials['periods'] or any(p['values'].get(k) is None
        for p in financials['periods'][:2] for k in ('revenue','profit_parent','operating_cashflow'))
    if needs_pdf:
        for doc in [d for d in official.get('accepted',[]) if '年度报告' in d['title'] and '摘要' not in d['title']][:2]:
            downloaded=await fetch_document(doc,settings.root/'data/financial-model/sources')
            fallbacks.append({'url':doc['url'],'status':downloaded['status']})
            if downloaded['status']!='body_retrieved': continue
            year=re.search(r'20\d{2}',doc['title'])
            if not year: continue
            period=year[0]+('-06-30' if '半年度报告' in doc['title'] else '-12-31')
            if period>cutoff: continue
            text=__import__('pathlib').Path(downloaded['local_file']).with_suffix('.txt').read_text(encoding='utf-8')
            checked=reconcile_report(text,{'name':name,'symbol':code,'periods':[{'period':period,'values':{}}]},period)
            values={x['field']:x['current_cny'] for x in checked.get('checks',[]) if 'current_cny' in x}
            fallbacks[-1]['parse_status']=checked['status']
            if values:
                old=next((p for p in financials['periods'] if p['period']==period),None)
                source={'url':doc['url'],'report':'official_pdf_partial_extraction'}
                if old:
                    for key,v in values.items():
                        if old['values'].get(key) is None: old['values'][key]=v
                    old.setdefault('source_tables',[]).append(source)
                else: financials['periods'].append({'period':period,'values':values,'published_at':doc.get('published_at'),'source_tables':[source]})
    financials['periods'].sort(key=lambda p:p['period'],reverse=True)
    analysis=statement_analysis(financials)
    result={'symbol':code,'name':name,'as_of':cutoff,'analysis':analysis,'requests':requests,
        'official_disclosures':official,'fallbacks':fallbacks,'dcf':None,
        'coverage_note':'已披露不等于本轮已解析；接口失败、原文获取和字段解析分别记录。'}
    annual=next((r for r in analysis['rows'] if r['period'].endswith('-12-31')),None)
    if assumptions and annual:
        result['dcf']=dcf_scenarios(annual['values'].get('revenue'),annual['period'],assumptions)
    result['dcf_status']='modeled_scenarios' if result['dcf'] else 'needs_explicit_assumptions' if annual else 'annual_revenue_not_parsed'
    return result
