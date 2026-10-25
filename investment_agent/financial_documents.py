"""Bounded local PDF evidence extraction. Ambiguous cells stay unparsed."""
from pathlib import Path
import hashlib
import re
from .common import DataError

ALIASES={'货币资金':'cash','应收账款':'receivables','存货':'inventory','固定资产':'ppe',
 '短期借款':'short_debt','长期借款':'long_debt','应付债券':'bonds','应付账款':'payables',
 '资产总计':'assets','负债合计':'liabilities','所有者权益合计':'equity_total','少数股东权益':'minority_interest',
 '营业收入':'revenue','营业成本':'cost_of_sales','研发费用':'rd_expense','财务费用':'finance_expense',
 '其中：利息费用':'interest_expense','利润总额':'profit_total','所得税费用':'income_tax','净利润':'net_income',
 '经营活动产生的现金流量净额':'operating_cashflow','购建固定资产、无形资产和其他长期资产支付的现金':'capex_cash'}
NOTE_TERMS={'subsidy':'政府补助','rd_capitalization':'开发支出','pledge':'质押','customer_concentration':'前五名客户',
 'related_party':'关联交易','restricted_cash':'受限','audit':'审计意见','guarantees':'担保','impairment':'商誉减值'}


def parse_amount(text):
    if text is None: return None
    text=str(text).replace(',','').replace('，','').replace(' ','').replace('\n','').strip()
    if text.startswith('(') and text.endswith(')'): text='-'+text[1:-1]
    return float(text) if re.fullmatch(r'-?\d+(?:\.\d+)?',text) else None


def extract_pdf(path,period,issuer,source_url='',published_at=None):
    import pdfplumber
    path=Path(path)
    if path.stat().st_size>40_000_000 or path.read_bytes()[:4]!=b'%PDF': raise DataError('PDF_INPUT','仅接受40MB以内PDF财报。')
    if not re.fullmatch(r'20\d{2}-(03-31|06-30|09-30|12-31)',period): raise DataError('PDF_PERIOD','需明确有效报告期。')
    values={}; cells=[]; conflicts=[]; notes=[]; pages_read=0; text_chars=0
    with pdfplumber.open(path) as pdf:
        # Identity check against front matter; uploaded files remain user-supplied evidence.
        head=''.join((p.extract_text() or '') for p in pdf.pages[:3]).replace(' ','')
        if issuer.replace(' ','') not in head: raise DataError('PDF_ISSUER','PDF首页无法核对发行人，未自动导入数值。')
        statement_page=-100; current_unit=None; current_column=None; column_count=None
        for index,page in enumerate(pdf.pages[:180]):
            text=page.extract_text() or ''; pages_read+=1; text_chars+=len(text)
            for key,term in NOTE_TERMS.items():
                pos=text.find(term)
                if pos>=0 and len([n for n in notes if n['factor']==key])<3:
                    notes.append({'factor':key,'page':index+1,'excerpt':text[max(0,pos-80):pos+400],
                                  'status':'需结合完整附注核验，不是风险结论','url':source_url+f'#page={index+1}' if source_url else ''})
            if re.search(r'母公司(?:资产负债表|利润表|现金流量表)|合并所有者权益变动表|财务报表附注',text[:900]):
                statement_page=-100; current_column=None
            if re.search(r'合并(?:资产负债表|利润表|现金流量表)',text):
                statement_page=index; current_column=None
            if index-statement_page>4: continue
            unit=re.search(r'单位[：:]?\s*(?:人民币)?\s*([亿万千]?元)',text)
            if unit: current_unit=unit[1]
            if not current_unit: continue
            multiplier={'元':1,'千元':1e3,'万元':1e4,'亿元':1e8}.get(current_unit)
            if multiplier is None: continue
            for table in page.extract_tables():
                # Current-year column must be explicitly identifiable, not guessed by position.
                header=next((r for r in table[:5] if any(period[:4] in str(c) for c in r)),None)
                if header is not None:
                    positions=[i for i,c in enumerate(header) if period[:4] in str(c)]
                    if len(positions)!=1: continue
                    current_column=positions[0]; column_count=len(header)
                if current_column is None or not table or len(table[0])!=column_count: continue
                column=current_column
                for row in table:
                    if not row or column>=len(row): continue
                    label=re.sub(r'\s+','',str(row[0] or '')); label=re.sub(r'^[一二三四五六七八九十\d]+[、.．]','',label)
                    label=re.sub(r'[（(].*?[）)]','',label)
                    if label.startswith('其中：') and label!='其中：利息费用': label=label[3:]
                    if label.startswith('减：'): label=label[2:]
                    key=ALIASES.get(label); number=parse_amount(row[column])
                    if not key or number is None: continue
                    number*=multiplier
                    if key in values and abs(values[key]-number)>max(1,abs(number)*1e-6):
                        conflicts.append(key); values.pop(key,None); continue
                    if key in conflicts: continue
                    values[key]=number
                    cells.append({'field':key,'value':number,'page':index+1,'label':label,'unit':current_unit,
                                  'url':source_url+f'#page={index+1}' if source_url else ''})
    expense_convention='positive_expense'
    if values.get('cost_of_sales',0)<0:
        expense_convention='source_signed_expenses_normalized'
        for key in ('cost_of_sales','rd_expense','interest_expense','income_tax'):
            if key in values: values[key]=-values[key]
    if values.get('capex_cash',0)<0:
        values['capex_cash']=-values['capex_cash']
    return {'period':period,'published_at':published_at,'values':values,'scope':'consolidated','currency':'CNY','expense_convention':expense_convention,
            'source_tables':[{'url':source_url,'report':'pdf_table_extraction','sha256':hashlib.sha256(path.read_bytes()).hexdigest()}],
            'cells':cells,'notes':notes,'conflicts':conflicts,'pages_read':pages_read,
            'status':'parsed_partial' if values else 'ocr_required' if text_chars<100 else 'unparsed',
            'limits':['最多读取180页；仅自动识别明确标注单位和当前年列的合并主表。',
                      '扫描页需OCR或人工复核，不把无文本当作未披露。附注摘录只作为调查入口。']}
