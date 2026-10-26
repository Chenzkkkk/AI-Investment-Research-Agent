"""CFA-informed research registry: executable mathematics and evidence workflows."""
from dataclasses import dataclass, field, asdict, replace
from pathlib import Path
import json
import re
from .common import DataError, now
from . import professional_models as models

CFA='https://www.cfainstitute.org/programs/cfa-program/candidate-resources/cbok'
MOF='https://kjs.mof.gov.cn/zt/kjzzss/kuaijizhunzeshishi/'


@dataclass(frozen=True)
class SkillSpec:
    name: str
    title: str
    domain: str
    kind: str
    description: str
    inputs: str
    checks: tuple
    references: tuple = (CFA,)
    version: str = '1.0.0'
    cost: str = '本地计算免费；数据请求与模型受统一任务预算约束'
    status: str = 'executable'


@dataclass
class SkillResult:
    skill: str
    data: dict
    sources: list = field(default_factory=list)
    assumptions: dict = field(default_factory=dict)
    issues: list = field(default_factory=list)
    version: str = models.VERSION
    as_of: str = field(default_factory=lambda:now()[:10])


SPECS=[
 SkillSpec('statistical-analysis','统计与回归','数量方法','calculation','执行有截距OLS描述分析，拒绝共线和缺失输入，不把相关解释为因果。','features,target,feature_names可选',('时间对齐','共线性','样本内与样本外','因果限制')),
 SkillSpec('exposure-stress','敞口压力测试','固定收益与风险管理','calculation','按明确净敞口计算利率、汇率、商品或组合冲击的一阶情景。','exposures字典,shocks数组{name,changes,basis}',('净敞口非名义本金','冲击依据','非线性遗漏')),
 SkillSpec('financial-quality','财报与现金质量','财务报表','data','取得三表，拆分季度/TTM并检查现金质量；不把未解析说成未披露。','code,name',('合并口径','五年与八季覆盖','补助与非经常损益','审计与附注','会计估计变化'),(CFA,MOF)),
 SkillSpec('capital-allocation','资本配置与ROIC','公司金融','calculation','计算税后经营利润、平均投入资本、ROIC-WACC及经济利润。','ebit,tax_rate,debt_begin,debt_end,equity_begin,equity_end,cash_begin,cash_end,wacc',('年度口径','金融企业不适用','经营与非经营现金')),
 SkillSpec('three-statement','三表联动预测','财务报表','calculation','驱动式年度三表情景；显式融资缺口，不隐式补平。','base,drivers；结构见方法说明',('期初平衡','假设依据','现金与权益滚动')),
 SkillSpec('valuation','估值与情景','权益投资','calculation','FCFF/FCFE、DDM、剩余收益、可比估值与企业价值到股权桥接。','method及对应模型输入；先读取方法说明',('行业适用性','历史/假设分离','终期稳态','股数及净债务')),
 SkillSpec('portfolio-risk','组合风险','组合管理','calculation','已对齐收益矩阵的集中度、波动、回撤和跟踪误差。','returns,weights,benchmark可选',('同日同口径','费用未计','非可执行回测')),
 SkillSpec('macro-policy','中国宏观与政策','经济学','workflow','检查增长、价格、信用、利率、汇率、贸易和政策执行条件。','query,as_of可选',('政策阶段与实施日期','预算/订单区别','经济数据修订','公司实际敞口')),
 SkillSpec('business-industry','商业模式与产业','权益投资','workflow','研究需求、供给、竞争、产业链、单位经济与商业化。','query,as_of可选',('真实业务收入','客户和供应商集中','竞争替代','增长与资本强度')),
 SkillSpec('governance','治理与风险事件','公司金融','workflow','核查控制权、质押、关联交易、担保、稀释、审计与监管事件。','query,as_of可选',('公告与问询原文','比例分母','承诺与执行区别','股东利益一致性')),
 SkillSpec('debt-exposure','债务与跨资产敞口','固定收益与风险管理','workflow','检查债务到期、再融资、利率、外汇及原料敏感性。','query,as_of可选',('债务期限与受限现金','汇率净敞口','套保名义本金非实际风险','压力情景')),
 SkillSpec('frontier-commercialization','前沿技术与商业化','另类投资','workflow','追踪产品、融资、订单、交付和财务兑现，区分直接/间接/概念关联。','query,as_of可选',('原始发布','交付证据','单位经济','商业化条件')),
 SkillSpec('counter-evidence','反证与行为偏差','职业伦理','workflow','针对核心投资假设寻找矛盾和失效条件；检查确认偏差与幸存者偏差。','query,as_of可选',('独立证据','替代解释','证据缺失不是负面事实','可观察失效条件')),
 SkillSpec('technical-analysis','技术与量价','数量方法','data','复用真实历史快照和确定性指标；收益评价需交易日与复权一致。','code,name可选',('价格日期','复权口径','停牌/流动性','量价不等于资金流')),
]
SPECS=[replace(s,status='limited') if s.kind=='workflow' else s for s in SPECS]
REGISTRY={s.name:s for s in SPECS}


def catalog():
    return [asdict(s) for s in SPECS]


def method(name):
    if name not in REGISTRY: raise DataError('SKILL_UNKNOWN','未注册的专业Skill。')
    spec=REGISTRY[name]
    path=Path(__file__).parent/'skill_methods'/name/'SKILL.md'
    from .china_rules import applicable_rules
    return {**asdict(spec),'instructions':path.read_text(encoding='utf-8') if path.exists() else spec.description,
            'china_method_sources':applicable_rules(now()[:10]) if name in ('macro-policy','financial-quality','governance','technical-analysis') else None}


BASE_FACTORS={
 'business':'商业模式与真实收入', 'policy':'政策与行业传导', 'financial':'财务质量与现金流',
 'governance':'治理、审计与股东风险', 'valuation':'估值与假设', 'market':'技术、流动性与市场风险',
 'counter':'反面证据与失效条件'}


@dataclass
class CoverageChecklist:
    items: dict = field(default_factory=lambda:{k:{'label':v,'status':'not_run','claim':'','evidence_ids':[]} for k,v in BASE_FACTORS.items()})

    def observe(self, trace):
        """Execution is derived from actual payloads, never from model promises."""
        mapping={'business-industry':'business','macro-policy':'policy','governance':'governance',
                 'counter-evidence':'counter','technical-analysis':'market','financial-quality':'financial','valuation':'valuation'}
        for entry in trace:
            result=entry.get('result',{}); data=result.get('data') or {}
            if not isinstance(data,dict): continue
            tool=entry.get('tool'); skill=data.get('skill') or entry.get('arguments',{}).get('name')
            payload=data.get('data',{}) if data.get('skill') else data
            factor='financial' if tool=='financial_model' else mapping.get(skill) if tool=='execute_professional_skill' else 'market' if tool in ('load_history','analyze_factors') else 'counter' if tool=='research_perspective' and entry.get('arguments',{}).get('role') in ('bear','review') else None
            if not factor: continue
            substantive=result.get('ok') is True and bool(payload) and payload.get('evidence_status')!='missing'
            if factor=='financial': substantive=substantive and bool(payload.get('analysis',{}).get('rows'))
            current=self.items[factor]
            if current['status'] in ('checked','conflict'): continue
            status='calculated' if substantive and factor in ('financial','valuation','market') else 'retrieved' if substantive else 'missing'
            if current['status'] in ('calculated','retrieved') and status=='missing': continue
            self.items[factor]={**current,'status':status,'evidence_ids':list(dict.fromkeys(current['evidence_ids']+[entry['id']])),
                'claim':{'calculated':'已执行取数与程序计算；计算完成不等于全部假设和原文已交叉核验。',
                         'retrieved':'已执行调查并取得材料；仍需判断证据是否支持具体结论。',
                         'missing':'已执行调查，但未取得所需数据；详见来源错误及解析缺口。'}[status]}
            if factor=='financial' and any('enterprise_value' in v for v in (payload.get('dcf') or {}).get('scenarios',{}).values()):
                previous=self.items['valuation']
                if previous['status'] not in ('checked','conflict'):
                    self.items['valuation']={**previous,'status':'calculated','claim':'已运行现金流估值情景，假设依据与股权桥接单列复核。','evidence_ids':[entry['id']]}
        return self.summary()

    def update(self, entries, trace):
        allowed={'checked','conflict','missing','not_applicable','not_run'}
        known={x['id']:x for x in trace}
        for item in entries:
            key=item.get('factor'); status=item.get('status')
            if not key or status not in allowed: raise DataError('COVERAGE_STATUS','覆盖状态或因素无效。')
            refs=item.get('evidence_ids',[])
            if status in ('checked','conflict'):
                if not refs or not item.get('claim') or any(x not in known or known[x]['result'].get('ok') is not True for x in refs):
                    raise DataError('COVERAGE_EVIDENCE','已核查/冲突必须关联本轮成功工具证据和具体发现。')
                # A workflow method note is not substantive evidence.
                def empty_evidence(result):
                    data=result.get('data')
                    if result.get('source') in ('research_method','coverage_audit'): return True
                    if not data: return True
                    nested=data.get('data',{}) if isinstance(data,dict) else {}
                    return isinstance(nested,dict) and nested.get('evidence_status')=='missing'
                if all(empty_evidence(known[x]['result']) for x in refs):
                    raise DataError('COVERAGE_EMPTY','调查框架不能充当事实证据。')
            if status=='not_applicable' and not item.get('claim'): raise DataError('COVERAGE_REASON','不适用需要说明理由。')
            self.items[key]={'label':BASE_FACTORS.get(key,item.get('label',key)), 'status':status,
                             'claim':item.get('claim',''),'evidence_ids':refs,'watch_next':item.get('watch_next','')}
        return self.summary()

    def summary(self):
        items=[{'factor':k,**v} for k,v in self.items.items()]
        unknown=[x for x in items if x['status'] not in ('checked','not_applicable')]
        return {'items':items,'important_unknowns':unknown[:3],
                'quality_gate':'incomplete' if unknown else 'covered_requires_judgment',
                'note':'状态为研究覆盖及证据关联检查，不是自动语义核验、真实概率或收益预测。'}


def collect_sources(value):
    found=[]
    if isinstance(value,dict):
        for k,v in value.items():
            if k in ('url','source_url') and isinstance(v,str) and v.startswith(('https://','http://')): found.append(v)
            else: found+=collect_sources(v)
    elif isinstance(value,list):
        for v in value: found+=collect_sources(v)
    return list(dict.fromkeys(found))[:100]


async def execute(agent, name, inputs):
    spec=REGISTRY.get(name)
    if not spec: raise DataError('SKILL_UNKNOWN','只能调用已注册的专业Skills。')
    sources=[]; issues=[]; assumptions={}
    if name=='financial-quality':
        data=await agent.financial_data(inputs['code'],inputs['name'])
        sources=collect_sources(data)
        issues=[{'type':'document_coverage','status':d.get('status'),'conflicts':d.get('conflicts',[]),'limits':d.get('limits',[])} for d in data.get('document_analysis',[])]
    elif name=='technical-analysis':
        from datetime import date,timedelta
        # The OHLCV contract accepts completed daily bars only after 16:00 CST.
        stamp=now(); end_day=date.fromisoformat(stamp[:10])
        if stamp[11:13]<'16':end_day-=timedelta(days=1)
        end=end_day.isoformat(); start=(end_day-timedelta(days=365)).isoformat()
        from .quant_data import collect_history
        from .quant import QuantLab
        snapshot=await collect_history(agent.service,[inputs['code']],start,end)
        if not snapshot.get('ok'): return snapshot
        dataset_id=snapshot['data']['id']
        data={'snapshot':snapshot,'factors':QuantLab(agent.settings.root).analyze(dataset_id),
              'instruction':'K线按原快照日期和复权展示。'}
        sources=collect_sources(snapshot)
    elif spec.kind=='workflow':
        from .intelligence.service import IntelligenceService
        query=inputs.get('query','').strip()
        if not query: raise DataError('SKILL_QUERY','调查需要主题或公司名称。')
        service=IntelligenceService(agent.settings.root)
        evidence=service.search_intelligence(query=query,as_of=inputs.get('as_of'))
        sources=collect_sources(evidence)
        from .china_research import discover_official
        official=await discover_official(agent.service,query,name,inputs.get('as_of'),inputs.get('code'),inputs.get('name'))
        sources=list(dict.fromkeys(sources+collect_sources(official)))
        substantive=bool(evidence.get('data')) or any(e.get('body') for e in official['evidence'])
        data={'method':method(name),'evidence':evidence,'official':official,'evidence_status':'retrieved' if substantive else 'missing',
              'next_actions':['核查原文与事件时间','必要时通过read_public_source读取官方原文','按检查项形成证据支持的判断'],
              'limits':['本地情报检索不是全网检索；无记录不表示事件不存在。']}
        if not substantive: issues.append({'type':'missing_evidence','message':'调查已执行但未取得足够正文证据，不等同于没有事件。'})
    else:
        assumptions={'classification':'explicit_scenario_inputs','inputs':inputs,
                     'note':'计算校验不等于假设或历史数值已经核实，需关联本轮证据。'}
        if name=='statistical-analysis': data=models.regression_analysis(**inputs)
        elif name=='exposure-stress': data=models.exposure_scenarios(**inputs)
        elif name=='capital-allocation': data=models.capital_analysis(inputs)
        elif name=='three-statement': data=models.three_statement(inputs['base'],inputs['drivers'])
        elif name=='portfolio-risk': data=models.portfolio_diagnostics(**inputs)
        elif name=='valuation':
            method_name=inputs.get('method'); p={k:v for k,v in inputs.items() if k!='method'}
            if inputs.get('sector') in ('bank','insurance','financial','restructuring'):
                raise DataError('VALUATION_SECTOR','该类型专用模型尚未验证，不套用通用估值。')
            p.pop('sector',None)
            if method_name=='cost_of_capital': data=models.cost_of_capital(**p)
            elif method_name=='FCFF':
                from .financial_model import dcf_scenarios
                data=dcf_scenarios(**p)
            elif method_name in ('FCFE','DDM'): data=models.discounted_equity(**p,method=method_name)
            elif method_name=='residual_income': data=models.residual_income(**p)
            elif method_name=='peers': data=models.peer_valuation(**p)
            elif method_name=='equity_bridge': data=models.equity_bridge(p)
            else: raise DataError('VALUATION_METHOD','支持FCFF/FCFE/DDM/residual_income/peers/equity_bridge。')
        else: raise DataError('SKILL_HANDLER','尚未实现的执行器。')
    result=SkillResult(name,data,sources,assumptions,issues)
    return {'ok':True,'source':'professional_skill','data':asdict(result),'data_time':result.as_of}
