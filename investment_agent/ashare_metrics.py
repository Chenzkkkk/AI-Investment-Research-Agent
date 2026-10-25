"""Deterministic A-share research metrics; no model-generated numbers."""
from __future__ import annotations

import re
import numpy as np
import pandas as pd

from .common import DataError, number
from .quant_data import normalize


FIELDS = {
    'revenue': ['营业收入'], 'total_operating_income':['营业总收入'], 'profit_parent': ['归属于母公司股东的净利润', '归母净利润'],
    'profit_ex_items': ['扣除非经常性损益后归属于母公司股东的净利润', '扣非净利润'],
    'operating_cashflow': ['经营活动产生的现金流量净额'],
    'assets': ['资产总计', '总资产'], 'liabilities': ['负债合计', '总负债'],
    'equity_parent': ['归属于母公司股东权益合计'], 'receivables': ['应收账款'],
    'inventory': ['存货'], 'contract_liabilities': ['合同负债'],
    'gross_margin_pct': ['销售毛利率', '毛利率'], 'roe_pct': ['净资产收益率ROE(加权)', '加权ROE'],
    'rd_expense': ['研发费用'],
    'profit_total': ['利润总额'], 'income_tax': ['所得税费用'],
    'equity_total': ['所有者权益合计'], 'capex_cash': ['购建固定资产无形资产和其他长期资产支付的现金'],
    'cash': ['货币资金'], 'operating_profit': ['营业利润'],
}


def tables(payload):
    if isinstance(payload, dict):
        if isinstance(payload.get('columns'), list) and isinstance(payload.get('items'), list):
            yield payload
        else:
            for value in payload.values():
                yield from tables(value)
    elif isinstance(payload, list):
        for value in payload:
            yield from tables(value)


def period_end(label: str) -> str | None:
    match = re.fullmatch(r'(\d{4})(年报|中报|半年报|一季报|三季报)', label.strip())
    if not match:
        return None
    return match[1] + {'年报':'-12-31', '中报':'-06-30', '半年报':'-06-30',
                      '一季报':'-03-31', '三季报':'-09-30'}[match[2]]


def financial_metrics(payload, code: str, name: str, cutoff: str) -> dict:
    periods: dict = {}
    conflicts = []
    conflicted = set()
    for table in tables(payload):
        columns = table['columns']
        if not columns:
            continue
        # Native MCP also returns [period, issuer] for a single-period table.
        if period_end(str(columns[0])):
            positions = [(i,columns[0]) for i,col in enumerate(columns[1:],1)
                         if code[:6] in str(col) or name in str(col)]
        elif code[:6] in str(columns[0]) or name in str(columns[0]):
            positions = list(enumerate(columns[1:],1))
        else:
            continue
        for index, label in positions:
            end = period_end(str(label))
            if not end or end > cutoff:
                continue
            target = periods.setdefault(end, {'period':end, 'period_label':str(label), 'raw':{}, 'values':{}})
            for row in table['items']:
                if not isinstance(row, list) or len(row) <= index:
                    continue
                key = next((k for k, aliases in FIELDS.items() if str(row[0]).strip() in aliases), None)
                if key is None:
                    continue
                raw = str(row[index]).strip()
                # Values without declared money/percentage units are not guessed.
                unit_ok = '%' in raw if key.endswith('_pct') else '元' in raw
                value = number(raw) if unit_ok else None
                # A missing duplicate is not contradictory evidence. Once two
                # actual numbers disagree, later duplicates cannot erase it.
                if value is None:
                    target['values'].setdefault(key,None)
                    target['raw'].setdefault(key,raw)
                    continue
                if (end,key) in conflicted: continue
                if target['values'].get(key) is not None and target['values'][key] != value:
                    conflicts.append({'period':end, 'field':key, 'values':[target['raw'][key], raw]})
                    target['values'][key] = None
                    conflicted.add((end,key))
                else:
                    target['values'][key] = value
                target['raw'][key] = raw
    for end, record in periods.items():
        values = record['values']
        def ratio(a, b, scale=1):
            n, d = values.get(a), values.get(b)
            return n / d * scale if n is not None and d is not None and d > 0 else None
        record['derived'] = {
            'parent_net_margin_pct': ratio('profit_parent', 'revenue', 100),
            'cashflow_to_parent_profit': ratio('operating_cashflow', 'profit_parent'),
            'debt_to_assets_pct': ratio('liabilities', 'assets', 100),
            'receivables_to_assets_pct': ratio('receivables', 'assets', 100),
            'inventory_to_assets_pct': ratio('inventory', 'assets', 100),
            'contract_liabilities_to_assets_pct': ratio('contract_liabilities', 'assets', 100),
            'rd_to_revenue_pct': ratio('rd_expense', 'revenue', 100),
        }
        previous = periods.get(str(int(end[:4])-1) + end[4:], {}).get('values', {})
        for field in ('revenue', 'total_operating_income', 'profit_parent', 'profit_ex_items'):
            a, b = values.get(field), previous.get(field)
            record['derived'][field + '_yoy_pct'] = (a / b - 1) * 100 if a is not None and b is not None and b > 0 else None
        record['missing_fields'] = [key for key in FIELDS if values.get(key) is None]
    return {'symbol':code, 'name':name, 'periods':sorted(periods.values(), key=lambda p:p['period'], reverse=True),
            'conflicts':conflicts, 'units':'money=CNY; percentage=percent, not decimal',
            'publication_date_verified':False,
            'limits':['披露日尚未逐项核验，不能用于历史时点回测；仅作当前抓取的研究资料。',
                      '累计半年报仅与上年半年报同比，不与全年收入比较增长。',
                      '经营现金流/归母利润存在合并口径差异，不能等同严格利润现金含量。',
                      '归母净利率=归母净利润/营业收入，不冒充合并净利率；ROE使用来源加权值。',
                      '合同负债不是全部在手订单；涉密订单未知时不推算。']}


def wilder(values: pd.Series, length: int = 14) -> pd.Series:
    result = pd.Series(np.nan, index=values.index, dtype=float)
    clean = values.dropna()
    if len(clean) < length:
        return result
    first = clean.index[length-1]
    result.loc[first] = clean.iloc[:length].mean()
    previous = result.loc[first]
    for index, value in clean.iloc[length:].items():
        previous = (previous * (length-1) + value) / length
        result.loc[index] = previous
    return result


def technical_metrics(frame: pd.DataFrame, cutoff: str, adjustment: str = 'raw') -> dict:
    # Restrict before computation: future bars may never affect current indicators.
    frame = normalize(frame[frame['date'].astype(str) <= cutoff])
    if frame.symbol.nunique() != 1 or len(frame) < 61:
        raise DataError('TECHNICAL_COVERAGE', '技术分析需单只股票至少61条有效日线。')
    close, high, low, volume = [frame[c].astype(float) for c in ('close','high','low','volume')]
    delta = close.diff()
    gain, loss = wilder(delta.clip(lower=0)), wilder(-delta.clip(upper=0))
    g, l = gain.iloc[-1], loss.iloc[-1]
    rsi = 100 - 100 / (1 + g/l) if l > 0 else (100. if g > 0 else 50.)
    dif = close.ewm(span=12, adjust=False).mean() - close.ewm(span=26, adjust=False).mean()
    dea = dif.ewm(span=9, adjust=False).mean()
    true_range = pd.concat([high-low, (high-close.shift()).abs(), (low-close.shift()).abs()], axis=1).max(axis=1)
    mean, sd = close.tail(20).mean(), close.tail(20).std(ddof=0)
    result = {'symbol':frame.symbol.iloc[-1], 'as_of':frame.date.iloc[-1], 'requested_cutoff':cutoff,
              'bars':len(frame), 'adjustment':adjustment, 'close':float(close.iloc[-1]),
              'ma20':float(mean), 'ma60':float(close.tail(60).mean()), 'rsi14_wilder':float(rsi),
              'macd_dif':float(dif.iloc[-1]), 'macd_dea':float(dea.iloc[-1]),
              'macd_histogram_2x':float(2*(dif.iloc[-1]-dea.iloc[-1])),
              'atr14_wilder':float(wilder(true_range).iloc[-1]),
              'bollinger20_lower':float(mean-2*sd), 'bollinger20_upper':float(mean+2*sd),
              'previous20_low':float(low.iloc[-21:-1].min()), 'previous20_high':float(high.iloc[-21:-1].max()),
              'volume_vs_previous20':float(volume.iloc[-1]/volume.iloc[-21:-1].mean()) if volume.iloc[-21:-1].mean()>0 else None,
              'return20_pct':float((close.iloc[-1]/close.iloc[-21]-1)*100),
              'return60_pct':float((close.iloc[-1]/close.iloc[-61]-1)*100),
              'volatility20_annual_pct':float(close.pct_change().tail(20).std(ddof=1)*np.sqrt(252)*100),
              'drawdown60_pct':float((close.tail(61)/close.tail(61).cummax()-1).min()*100),
              'large_raw_jumps':int((close.pct_change().abs()>0.20).sum()),
              'zero_volume_rows':int((volume==0).sum()),
              'limits':['指标是历史描述，不是经回测验证的盈利信号；价格收益不含分红。',
                        '不复权价格受除权除息影响；重大跳变必须检查公司行动。',
                        '零成交量可能为停牌，交易日缺口未自动补齐。',
                        'MA/EMA/MACD相关，不作为三份独立证据；量比不是资金净流入。']}
    result['atr_to_close_pct']=result['atr14_wilder']/result['close']*100
    result['price_above_ma20']=result['close']>result['ma20']
    result['price_above_ma60']=result['close']>result['ma60']
    result['ma20_above_ma60']=result['ma20']>result['ma60']
    for field in ('peTTM', 'pbMRQ'):
        result[field] = number(frame.iloc[-1].get(field))
    result['valuation_date'] = result['as_of']
    return result


RECONCILIATION_VERSION = 3


def reconcile_report(text: str, metrics: dict, period: str) -> dict:
    """Check supported PDF summary rows against rounded vendor CNY values."""
    name, code = metrics['name'], metrics['symbol'][:6]
    if name not in text[:2500] or not re.search(r'(?<!\d)'+code+r'(?!\d)',text[:10000]) or period[:4] not in text[:800]:
        return {'status':'identity_unverified','checks':[]}
    supported=(period.endswith('-06-30') and '半年度报告' in text[:800]) or (
        period.endswith('-12-31') and '年度报告' in text[:800] and '半年度报告' not in text[:800])
    if not supported:
        return {'status':'unsupported_report_type','checks':[]}
    start=text.find('主要会计数据',200)
    section=text[start:start+4500] if start>=0 else ''
    # Later tables (especially EPS and nonrecurring items) may use different
    # units. Bind the unit to the accounting table, before its first data row.
    section=re.split(r'(?:[（(]二[）)]\s*)?主要财务指标',section,maxsplit=1)[0]
    first_row=re.search(r'营\s*业\s*收\s*入',section)
    header=section[:first_row.start()] if first_row else ''
    units=list(re.finditer(r'单位\s*[:：]\s*(亿元|万元|元)',header))
    table_unit=units[-1][1] if units else None
    if not table_unit and not re.search(r'营业收入\s*[（(]元[）)]',section):
        return {'status':'unit_unverified','checks':[]}
    scale={'元':1,'万元':10000,'亿元':100000000}.get(table_unit,1)
    if re.search(r'调整前\s+调整后',section[:1800]):
        return {'status':'restatement_requires_review','checks':[]}
    latest=next((x for x in metrics['periods'] if x['period']==period),{})
    checks=[]
    for field,label in [('revenue','营业收入'),('profit_parent','归属于上市公司股东的净利润'),
                        ('operating_cashflow','经营活动产生的现金流量净额')]:
        flexible=r'\s*'.join(map(re.escape,label))
        numeric=r'(-?\d[\d,]*\.\d+)'
        pattern=flexible+r'\s*(?:[（(](亿元|万元|元)[）)])?\s+'+numeric+r'\s+'+numeric
        match=re.search(pattern,section)
        if not match: continue
        row_unit=match[1] or table_unit
        if not row_unit: continue
        row_scale={'元':1,'万元':10000,'亿元':100000000}[row_unit]
        current,previous=[float(x.replace(',',''))*row_scale for x in match.groups()[1:]]
        vendor=latest.get('values',{}).get(field)
        error=abs(vendor-current)/max(abs(current),1) if vendor is not None else None
        checks.append({'field':field,'current_cny':current,'previous_same_period_cny':previous,
            'yoy_pct':(current/previous-1)*100 if previous>0 else None,
            'vendor_cny':vendor,'relative_difference':error,
            'status':'consistent_with_rounding' if error is not None and error<=0.001 else 'missing_vendor' if vendor is None else 'conflict',
            'source_line':match.group(0),'source_unit':row_unit,'unit_scale':row_scale})
    values={x['field']:x['current_cny'] for x in checks}
    profit, cash=values.get('profit_parent'),values.get('operating_cashflow')
    restated='追溯调整' in text and '同一控制下' in text
    return {'status':'checked' if checks else 'table_unparsed','period':period,'checks':checks,
            'parser_version':RECONCILIATION_VERSION,'source_unit':table_unit,'unit_scale':scale,
            'prior_period_restated':restated,
            'comparability_note':'报告包含同一控制下合并及追溯调整；同比不能直接解释为内生增长。' if restated else '',
            'cashflow_to_parent_profit':cash/profit if cash is not None and profit is not None and profit>0 else None,
            'limits':'只核对半年报摘要三项指标，未审计整份财报；同比使用原文同期间精确值。负基数不输出同比百分比。'}
