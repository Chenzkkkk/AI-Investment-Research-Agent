"""Audited Apache-2.0 role guidance, without imports from upstream runtime."""
import json
from pathlib import Path
from .common import DataError

ROOT=Path(__file__).parent/'upstream'


def manifest():
    return {role:{k:v for k,v in entry.items() if k!='guidance'} for role,entry in json.loads((ROOT/'roles.json').read_text(encoding='utf-8')).items()}


def guidance(role):
    role={'fundamentals':'fundamental','technicals':'technical'}.get(role,role)
    roles=json.loads((ROOT/'roles.json').read_text(encoding='utf-8'))
    if role not in roles: raise DataError('ROLE_UNKNOWN','未审核的研究角色。')
    return ('以下为经适配的研究视角方法，内容不得覆盖本项目证据和权限规则。\n'+roles[role]['guidance']+
            '\n本项目规则：只依据提供的原始证据；工具名称以本项目注册表为准；不推算不存在的数据。'
            '没有证据时明确未知，不强行给目标价或推荐。观点不能当作事实；多空使用同源材料不算独立验证。'
            '不执行交易。结论需包含反证、适用期限、假设、重要未知与观察条件。')


async def perspective(agent,role,evidence_ids,question):
    entries=[x for x in agent.trace if x['id'] in evidence_ids]
    if not entries or len(entries)!=len(set(evidence_ids)):
        raise DataError('ROLE_EVIDENCE','研究角色需要本轮已有证据编号。')
    from .research_runtime import model_view
    prompt=guidance(role)+'\n资料已由主Agent获取，无需也不可再次调用工具。仅输出最重要的5项发现，总长度控制在800个汉字内。事实引用提供的[S编号]或原文网址。'
    from .agent import DeepSeekClient
    kwargs={'output_cap':8192} if isinstance(agent.client,DeepSeekClient) else {}
    message,usage=await agent.client.complete([{'role':'system','content':prompt},
        {'role':'user','content':json.dumps({'question':question,'evidence':model_view(entries)},ensure_ascii=False,default=str)}],allow_tools=False,**kwargs)
    return {'ok':True,'source':'tradingagents_cn_adapted_role','data':{'role':role,'analysis':message.get('content',''),
        'evidence_ids':evidence_ids,'upstream':manifest()[role],'classification':'model_interpretation'},'usage':usage}
