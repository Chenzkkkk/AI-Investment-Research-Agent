"""Versioned source index, never a substitute for dated execution-limit data."""
VERSION='1.0.0'
RULES=[
 {'id':'sse-trading-2026','version':'2026 revision','jurisdiction':'CN','exchange':'SSE',
  'scope':'上交所交易通用规则；具体证券及暂缓条款另行核查',
  'published_at':'2026-04-24','effective_from':'2026-07-06','effective_to':None,
  'source_url':'https://www.sse.com.cn/lawandrules/sselawsrules2025/trade/universal/c/c_20260424_10816492.shtml',
  'verified_at':'2026-10-25','status':'source_notice_verified',
  'limits':'官方通知明确部分条文暂缓实施；本条目未将附件自动转成下单规则，不适用于深交所或北交所。'},
 {'id':'cn-accounting','version':'source-index-1','jurisdiction':'CN','scope':'中国企业会计准则与期间适用解释',
  'published_at':None,'effective_from':None,'effective_to':None,
  'source_url':'https://kjs.mof.gov.cn/zt/kjzzss/kuaijizhunzeshishi/',
  'status':'reference_index','limits':'目录不是单项准则生效日期；应用前必须核查具体准则和解释。'},
 {'id':'cn-policy','version':'source-index-1','jurisdiction':'CN','scope':'政策原文及适用对象',
  'published_at':None,'effective_from':None,'effective_to':None,
  'source_url':'https://sousuo.www.gov.cn/zcwjk/','status':'reference_index',
  'limits':'必须分别核对发布、实施、细则、预算和执行结果；政策支持不等于订单。'}
]


def applicable_rules(as_of, exchange=None):
    from copy import deepcopy
    output=[]
    for rule in RULES:
        if exchange and rule.get('exchange') and rule['exchange']!=exchange: continue
        if rule.get('published_at') and rule['published_at']>as_of: continue
        item=deepcopy(rule)
        item['applicability']='reference_only' if not item['effective_from'] else 'effective_notice' if item['effective_from']<=as_of else 'not_effective_yet'
        output.append(item)
    return {'version':VERSION,'as_of':as_of,'entries':output,'limits':['不是完整历史规则库；未给定交易所时仅供来源发现。实际交易限制仍使用对应证券/日期数据。']}
