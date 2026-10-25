"""Generate a new evidence-based technology study. Never reuses historical reports."""
import asyncio
from dataclasses import replace
from pathlib import Path
import json
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from investment_agent.config import Settings
from investment_agent.common import now
from investment_agent.ashare_research import run_research

async def main():
    settings=replace(Settings.load(),enable_choice=False,enable_miaoxiang=False,
        investment_style='value',max_calls=120,research_pool_limit=150,research_top_n=5)
    result=await run_research(settings,[],now()[:10],progress=lambda x:print(x,flush=True),
        topic='AI、机器人与前沿科技',question='从科技相关行业广泛筛选，基于真实财报、现金流、估值与公告寻找最值得优先深入研究的长期价值候选。核验业务与AI或机器人是否真正相关；不把行业分类当主题收入证明，不强行推荐。')
    target=ROOT/'data/public-build/run.json'
    target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps({'run_id':result['run_id'],'status':result['status']},ensure_ascii=False),encoding='utf-8')
    print('Public study:',result['run_id'],result['status'],flush=True)

if __name__=='__main__': asyncio.run(main())
