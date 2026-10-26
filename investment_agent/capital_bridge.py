"""Disclosure-backed EV to equity bridge. Facts and valuation choices stay separate."""
import asyncio
import hashlib
import re
from pathlib import Path
from .common import DataError, Result, now, symbol
from .financial_documents import parse_amount

VERSION='1.0.0'
LABELS={'cash':'货币资金','cash_equivalents':'现金及现金等价物','short_debt':'短期借款',
 'long_debt':'长期借款','current_debt':'一年内到期的非流动负债','bonds':'应付债券',
 'lease_debt':'租赁负债','short_bonds':'短期应付债券','minority_interest':'少数股东权益',
 'share_capital':'股本金额','shares':'期末总股数','assets':'资产总计','liabilities':'负债合计',
 'equity_total':'所有者权益合计','deposits_current':'流动定期存款及存单',
 'deposits_maturing':'一年内到期的存单','deposits_long':'非流动定期存款及存单'}
MAIN={'货币资金':'cash','短期借款':'short_debt','长期借款':'long_debt',
 '一年内到期的非流动负债':'current_debt','应付债券':'bonds','租赁负债':'lease_debt',
 '少数股东权益':'minority_interest','实收资本（或股本）':'share_capital','股本':'share_capital',
 '资产总计':'assets','负债合计':'liabilities','所有者权益（或股东权益）合计':'equity_total',
 '所有者权益合计':'equity_total'}
DEBT=('short_debt','long_debt','current_debt','bonds','lease_debt','short_bonds')


def extract_bridge_pdf(path,period,issuer,url,published_at):
    """Read selected tables, with explicit column headers and continuation matching.

    A disclosed blank is only zero when the complete table reconciles or the
    corresponding note explicitly says not applicable. Missing rows stay missing.
    """
    import pypdf
    import pdfplumber
    path=Path(path)
    if path.stat().st_size>40_000_000: raise DataError('BRIDGE_PDF_SIZE','财报超过40MB，需分段导入。')
    pages=[p.extract_text() or '' for p in pypdf.PdfReader(path).pages[:400]]
    if issuer.replace(' ','') not in ''.join(pages[:3]).replace(' ',''): raise DataError('PDF_ISSUER','发行人不匹配。')
    year,month,day=map(int,period.split('-'))
    expected=rf'{year}\s*年\s*0?{month}\s*月\s*0?{day}\s*日'
    facts={};conflicts=[];notes=[];state=None;column=None;width=None;main_start=-100
    def put(key,value,page,label,classification='disclosed',excerpt=''):
        if value is None: return
        if key in facts and abs(facts[key]['value']-value)>max(1,abs(value)*1e-7):
            conflicts.append(key);facts.pop(key,None);return
        if key in conflicts:return
        facts[key]={'value':value,'label':LABELS.get(key,label),'unit':'股' if key=='shares' else '元',
            'period':period,'published_at':published_at,'page':page,'source_label':label,
            'url':url+f'#page={page}','status':classification,'excerpt':excerpt[:300]}
    with pdfplumber.open(path) as pdf:
        for i,text in enumerate(pages):
            compact=re.sub(r'\s+','',text)
            if re.search(r'合并资产负债表\s*20\d{2}',text):
                if not re.search(expected,text[:700]): continue
                state='balance';main_start=i;column=None;width=None
            if re.search(r'母公司资产负债表|合并利润表',text[:700]):state=None;column=None
            if state=='balance' and i-main_start>3: state=None
            # Note headings, not mere mentions inside accounting policies.
            for pattern,kind in [(r'\d+[、．]一年内到期的非流动资产','maturing'),
                (r'\d+[、．]其他流动资产','current'),(r'\d+[、．]其他非流动资产','long'),
                (r'\d+[、．]其他流动负债','short_bonds'),(r'现金和现金等价物的构成','cash')]:
                if re.search(pattern,compact):state=kind;column=None;width=None;main_start=i
            if state not in ('balance',None) and i-main_start>1:state=None
            if '应付债券' in compact and re.search(r'\d+、应付债券\(1\)应付债券□适用√不适用',compact):
                put('bonds',0,i+1,'应付债券','explicit_not_applicable','应付债券：不适用')
            if any(term in compact for term in ('可转换公司债券','库存股','所有权或使用权受限资产','股份总数')):
                for term in ('可转换公司债券','库存股','所有权或使用权受限资产'):
                    pos=compact.find(term)
                    if pos>=0:notes.append({'label':term,'excerpt':compact[max(0,pos-10):pos+320],'url':url+f'#page={i+1}'})
            if not state and '股份总数' not in text:continue
            combined=''.join(pages[max(0,i-1):i+1])
            unit=re.search(r'单位[：:]?\s*(?:人民币)?\s*([亿万千]?元)',combined)
            if not unit:continue
            scale={'元':1,'千元':1000,'万元':1e4,'亿元':1e8}[unit[1]]
            for table in pdf.pages[i].extract_tables():
                if not table:continue
                for row in table[:4]:
                    cols=[j for j,c in enumerate(row) if (state=='balance' and period[:4] in str(c)) or (state!='balance' and '期末余额' in str(c))]
                    if len(cols)==1:column=cols[0];width=len(row);break
                if state=='long' and len(table[0])==7 and '账面价值' in combined:
                    column=3;width=7 # explicit two-period gross/impairment/net layout
                for row in table:
                    label=re.sub(r'\s+','',str(row[0] or ''))
                    if label=='股份总数':
                        values=[parse_amount(c) for c in row[1:]]
                        v=next((v for v in reversed(values) if v is not None),None)
                        # Confirm a share count in the issuer's text; never assume par value is 1.
                        if v is not None:
                            literal=f'{v:,.0f}'
                            if re.search(re.escape(literal)+r'\s*股',''.join(pages)) or re.search(str(int(v))+r'\s*股',compact):
                                put('shares',v,i+1,label,'cross_checked_share_count')
                    if column is None or len(row)!=width or column>=len(row):continue
                    raw=row[column];v=parse_amount(raw)
                    if state=='balance' and label in MAIN:put(MAIN[label],v*scale if v is not None else None,i+1,label)
                    if state=='cash' and label.endswith('期末现金及现金等价物余额'):put('cash_equivalents',v*scale if v is not None else None,i+1,label)
                    if state in ('current','long') and label in ('大额存单及定期存款','定期存款及大额存单'):
                        put('deposits_'+state,v*scale if v is not None else None,i+1,label)
                    if label=='一年内到期的大额存单' and len(row)==3:
                        amount=parse_amount(row[1])
                        put('deposits_maturing',amount*scale if amount is not None else None,i+1,label)
                    if state=='short_bonds' and label=='短期应付债券':
                        if v is not None:put('short_bonds',v*scale,i+1,label)
                        elif raw in ('',None):
                            total=next((parse_amount(r[column]) for r in table if str(r[0]).strip()=='合计'),None)
                            other=[parse_amount(r[column]) for r in table[1:] if r is not row and str(r[0]).strip()!='合计']
                            if total is not None and abs(total-sum(x for x in other if x is not None))<.02:
                                put('short_bonds',0,i+1,label,'reconciled_blank','原表本期空白；其他明细之和与本期合计相符，零余额由表内勾稽得出。')
    get=lambda k:facts.get(k,{}).get('value')
    checks=[]
    if all(get(k) is not None for k in ('assets','liabilities','equity_total')):
        residual=get('assets')-get('liabilities')-get('equity_total')
        checks.append({'label':'合并资产负债表平衡','residual_cny':residual,'passed':abs(residual)<1})
        if abs(residual)>=1:conflicts+=['balance_sheet']
    required=list(DEBT)+['cash','cash_equivalents','minority_interest','shares']
    missing=[k for k in required if get(k) is None]
    debt=sum(get(k) for k in DEBT) if not any(get(k) is None for k in DEBT) else None
    return {'method_version':VERSION,'period':period,'published_at':published_at,'issuer':issuer,'url':url,
        'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'facts':facts,'notes':notes[:20],'checks':checks,
        'conflicts':list(set(conflicts)),'missing':missing,'debt':debt,
        'non_cash_equivalent_money':get('cash')-get('cash_equivalents') if get('cash') is not None and get('cash_equivalents') is not None else None,
        'net_debt_cash_equivalents':debt-get('cash_equivalents') if debt is not None and get('cash_equivalents') is not None else None,
        'status':'facts_ready' if not missing and not conflicts else 'incomplete',
        'limits':['使用该报告期末已披露余额，不冒充实时资产负债表。',
          '少数股东账面权益不等于其市场价值；企业价值桥接须另列估值倍数假设。',
          '定期存款、经营所需现金、金融子公司资产及股数后续变化需要明确的情景政策。']}


async def collect_bridge(settings,service,code,name,official):
    candidates=[]
    for d in official.get('accepted',[]):
        title=d['title']
        if '摘要' in title or '英文' in title:continue
        y=re.search(r'20\d{2}',title)
        if not y:continue
        suffix='06-30' if '半年度' in title else '09-30' if '第三季度' in title else '03-31' if '第一季度' in title else '12-31' if '年度报告' in title else None
        if suffix:candidates.append((y[0]+'-'+suffix,d))
    if not candidates:return {'status':'incomplete','missing':['官方完整财报'],'facts':{}}
    period,doc=max(candidates,key=lambda x:x[0])
    async def action():
        from .ashare_sources import fetch_document
        f=await fetch_document(doc,settings.root/'data/financial-model/sources')
        if f.get('status')!='body_retrieved':raise DataError('BRIDGE_DOCUMENT','官方完整财报取回失败。')
        data=await asyncio.to_thread(extract_bridge_pdf,f['local_file'],period,name,doc['url'],doc.get('published_at'))
        data['symbol']=symbol(code)
        return Result(source='official_filing_capital_bridge',data=data,metadata={'url':doc['url']})
    result=await service.fetch('public',{'capital_bridge_v2':doc['url']},action,ttl=86400)
    return result.data if result.ok else {'status':'incomplete','facts':{},'missing':['资本结构解析'],'error':result.error}


def bridge_scenarios(dcf,packet,policy,quote=None):
    """Explicit, reproducible scenarios; no missing-value zero defaults."""
    required=['operating_cash_reserve','deposit_recovery','minority_multiple','additional_claims','share_dilution','margin_required','basis']
    if any(k not in policy for k in required):raise DataError('BRIDGE_POLICY','必须提供经营现金、存单回收、少数股东、其他索偿、稀释、安全边际及依据。')
    if packet.get('status')!='facts_ready':return {'status':'incomplete','missing':packet.get('missing',[]),'conflicts':packet.get('conflicts',[]),'rows':[]}
    if policy.get('cashflow_excludes_nonoperating') is not True:
        raise DataError('FCFF_SCOPE','加回非经营资产前须明确FCFF已剔除存款利息及投资收益，并解释经营利润率依据，防止重复估值。')
    from datetime import date
    try: valuation_date=date.fromisoformat(policy['valuation_date'])
    except (KeyError,TypeError,ValueError): raise DataError('VALUATION_DATE','须明确情景估值日期；未来一年现金流由该日开始，不把历史营收期当估值时点。')
    if valuation_date<date.fromisoformat(packet['period']) or valuation_date>date.fromisoformat(now()[:10]):
        raise DataError('VALUATION_DATE','估值日期不能早于资产负债表日或晚于今天。')
    for k in required[:-1]:
        v=policy[k]
        if not isinstance(v,(int,float)) or isinstance(v,bool) or not __import__('math').isfinite(v) or v<0:raise DataError('BRIDGE_POLICY','假设必须为非负有限数。')
    if not 0<=policy['deposit_recovery']<=1 or not 0<=policy['margin_required']<1 or not policy['basis'].strip():raise DataError('BRIDGE_POLICY','折扣/安全边际或依据不合规。')
    f=packet['facts'];cash=f['cash_equivalents']['value']
    if policy['operating_cash_reserve']>cash:raise DataError('CASH_RESERVE','经营现金保留额超过现金及现金等价物。')
    available=[k for k in ('deposits_current','deposits_maturing','deposits_long') if k in f]
    deposits=policy.get('deposit_categories',available)
    if not isinstance(deposits,list) or len(set(deposits))!=len(deposits) or any(k not in available for k in deposits):
        raise DataError('DEPOSIT_SCOPE','加回存单类别必须来自已取得的财报项目，不能重复或补造。')
    other_assets=sum(f[k]['value'] for k in deposits)*policy['deposit_recovery']
    shares=f['shares']['value']+policy['share_dilution']
    if shares<=0:raise DataError('SHARES','股数必须为正。')
    adjustments={'debt':packet['debt'],'excess_cash':cash-policy['operating_cash_reserve'],
      'nonoperating_deposits':other_assets,'minority_value':f['minority_interest']['value']*policy['minority_multiple'],
      'additional_claims':policy['additional_claims'],'shares':shares}
    rows=[]
    for label,v in dcf.get('scenarios',{}).items():
        ev=v.get('enterprise_value')
        if ev is None:continue
        equity=ev-adjustments['debt']+adjustments['excess_cash']+other_assets-adjustments['minority_value']-policy['additional_claims']
        per_share=equity/shares
        price=(quote or {}).get('price')
        valid_price=isinstance(price,(int,float)) and not isinstance(price,bool) and __import__('math').isfinite(price) and price>0 and bool((quote or {}).get('as_of')) and bool((quote or {}).get('evidence_id'))
        rows.append({'scenario':label,'enterprise_value':ev,'equity_value':equity,'value_per_share':per_share,
          'margin_of_safety':1-price/per_share if valid_price and per_share>0 else None,
          'entry_price_at_required_margin':per_share*(1-policy['margin_required']) if per_share>0 else None})
    return {'status':'conditional_scenarios','rows':rows,'adjustments':adjustments,'policy':policy,'quote':quote,
      'deposit_items':[{**f[k],'key':k} for k in deposits],
      'valuation_date':valuation_date.isoformat(),'balance_date':packet['period'],'share_date':packet['period'],'sources':[v['url'] for v in f.values()],
      'limits':['每股数值是显式假设下的参考区间，不是保证兑现的目标价。',
        '采用披露期末总股数，不将加权平均EPS股数代替总股数；期后注销、增发和期权变化单列复核。',
        '只加回已列明且不在经营现金流中的存单；其他金融资产和股权投资未估值，不自动按总资产加回。',
        '包含租赁债务；FCFF应采用与租赁融资一致的现金流口径，不能同时重复扣除租金和租赁融资。',
        '少数股东市场价值、存单可支配性及潜在索偿为情景假设，不是财报已经确认的市场价值。']}


def bridge_markdown(packet,valuation=None):
    from .report_presentation import markdown_link
    lines=['### 企业价值到每股价值',
        '股权价值 = 经营企业价值 − 有息债务 + 可分配现金 + 明确列示的非经营资产 − 少数股东估值 − 其他索偿。',
        '资产负债表日：'+str(packet.get('period','未取得'))+'；金额单位：元。',
        '| 披露项目 | 数值 | 原始依据 |','|---|---:|---|']
    for k,v in packet.get('facts',{}).items():
        lines.append(f"| {v['label']}（{v['unit']}） | {v['value']:,.2f} | {markdown_link('财报第'+str(v['page'])+'页',v['url'])} |")
    lines.append('')
    if packet.get('missing'):lines.append('待解析项目：'+'、'.join(LABELS.get(k,k) for k in packet['missing']))
    if packet.get('conflicts'):lines.append('口径冲突：'+'、'.join(packet['conflicts']))
    if valuation and valuation.get('rows'):
        lines+=['**情景每股价值（元/股）**，估值日 '+valuation['valuation_date']+'。历史完整年度营收作为预测起点，预测第一年为估值日后一年。',
            '', '| 情景 | 每股价值 | 安全边际 | 要求安全边际后的参考买入上限 |','|---|---:|---:|---:|']
        for r in valuation['rows']:
            margin=r['margin_of_safety'];entry=r['entry_price_at_required_margin']
            lines.append(f"| {r['scenario']} | {r['value_per_share']:,.2f} | "+(f'{margin:.1%}' if margin is not None else '缺已核对价格')+' | '+(f'{entry:,.2f}' if entry is not None else '不适用')+' |')
        lines+=['','**桥接假设依据**：'+valuation['policy']['basis'], '',
            '安全边际 = 1 − 参考股价 / 每股价值；负值表示参考股价高于该情景价值，不能解释为价格下跌幅度。',
            '**本次实际加回范围**：'+('、'.join(LABELS.get(x['key'],x['key']) for x in valuation.get('deposit_items',[])) or '没有加回存单；旧版本请按计算输入复核')+'；统一回收比例 '+format(valuation['policy']['deposit_recovery'],'.1%')+'。',
            '；'.join(f'{label} {valuation["adjustments"][k]:,.2f}' for k,label in [('debt','有息债务'),('excess_cash','可分配现金'),('nonoperating_deposits','加回存单'),('minority_value','少数股东估值'),('additional_claims','其他索偿'),('shares','股数')]),
            *['- '+x for x in valuation['limits']]]
    else:lines.append('资本结构与原文已列示；每股估值需明确经营现金保留、非经营资产、少数股东及稀释假设，不能把账面余额直接当市场价值。')
    return '\n\n'.join(lines[:3])+'\n\n'+'\n'.join(lines[3:])
