import argparse
import asyncio
import importlib.util
import json
from pathlib import Path
import sys

import httpx

from .agent import ResearchAgent
from .choice import ChoiceProvider
from .common import DataError, failed, now
from .config import Settings
from .service import DataService


async def execute(args, settings: Settings) -> dict:
    if args.command in {"history", "quant", "import-csv", "enrich", "enrich-limits"}:
        from .quant import QuantLab
        from .quant_data import collect_history
        import pandas as pd
        lab = QuantLab(settings.root)
        if args.command == "enrich":
            from .data_quality import enrich_dataset
            return await enrich_dataset(DataService(settings),args.dataset_id)
        if args.command == "enrich-limits":
            from .data_quality import enrich_execution_limits
            return await enrich_execution_limits(DataService(settings),args.dataset_id)
        if args.command == "history":
            return await collect_history(DataService(settings),args.codes,args.start,args.end,args.source)
        if args.command == "import-csv":
            return lab.store.save(pd.read_csv(args.path,dtype={"symbol":str,"code":str}), source=args.source,
                adjustment=args.adjustment,synthetic=args.synthetic,provenance={"file_name":Path(args.path).name})
        options = json.loads(args.options)
        methods = {"catalog":lab.catalog,"factors":lab.analyze,"backtest":lab.backtest,"portfolio":lab.portfolio,
            "plan":lab.rebalance_plan,"daily":lab.daily,"predict":lab.predict,"review":lab.evaluate,"create":lab.book.create,"trade":lab.paper_trade}
        return methods[args.operation](**options)
    if args.command == "ask":
        return await ResearchAgent(settings).ask(args.question)
    if args.command == "quote":
        return (await DataService(settings).stock(args.code, source=args.source)).dict()
    if args.command == "skill":
        return (await DataService(settings).skill(args.name, args.query, args.stock_name)).dict()
    if args.command == "screen":
        return await ResearchAgent(settings).dispatch("screen_stocks", {})
    if args.command == "compare":
        return await DataService(settings).compare(args.code, args.sources)
    if args.command == "choice-check":
        from scripts.choice_sdk_check import check
        result, code = check(login=True)
        if code or not args.data:
            return result
        settings.enable_choice = True
        provider = ChoiceProvider(settings)
        checks = {}
        for operation in ("calendar", "quote", "history", "fundamentals"):
            try:
                checks[operation] = provider.request(operation).dict()
            except Exception as exc:
                checks[operation] = failed("choice", exc).dict()
                break
        output = {"checked_at": now(), "login": result, "checks": checks,
                  "note": "未自动启用；确认数据权限后在.env设置 ENABLE_CHOICE=true 并重启。"}
        (settings.root / "docs/choice_permissions.json").write_text(json.dumps(output, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        return output
    if args.command == "choice-query":
        service = DataService(settings)
        params = {"operation": args.operation, "code": args.code, "start": args.start, "end": args.end,
                  "report_date": args.report_date or None, "indicator": args.indicator}
        params = {k: v for k, v in params.items() if v is not None}
        return (await service.fetch("choice", params, lambda: asyncio.to_thread(service.choice.request, **params))).dict()
    if args.command == "choice-edb-search":
        if not settings.enable_choice:
            raise DataError("DISABLED", "Choice 未启用，不触发指标搜索。")
        async with httpx.AsyncClient(timeout=25) as client:
            response = await client.get("https://mxapi.eastmoney.com/edb/indicator/search", params={"query": args.query})
        if response.status_code != 200 or response.json().get("code") != 200:
            raise DataError("EDB_SEARCH_FAILED", "官方宏观指标搜索失败。")
        rows = response.json().get("data", [])[:10]
        path = settings.root / "data/choice_edb_catalog.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        catalog = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        catalog.update({x["logicId"]: {k: x.get(k) for k in ("singleName", "unit", "region", "updateFrequencyCN")}
                        for x in rows if x.get("logicId")})
        path.write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"indicators": rows, "note": "先核对名称/地区/频率/单位，再将返回的logicId用于Choice宏观查询。"}
    if args.command == "doctor":
        result = {"python": sys.executable, "model": settings.model,
                  "thinking_enabled": True, "reasoning_effort": settings.reasoning_effort,
                  "max_output_tokens": settings.max_output_tokens,
                  "credentials": {"deepseek_present": bool(settings.deepseek_key), "mcp_present": bool(settings.mcp_key),
                                  "skills_present": bool(settings.skills_key)},
                  "sources": {"miaoxiang": settings.enable_miaoxiang, "choice": settings.enable_choice,
                              "public": settings.enable_public},
                  "choice_import_discoverable": importlib.util.find_spec("EmQuantAPI") is not None,
                  "skills_count": len(list((settings.root / "mx-skills").glob("*/SKILL.md")))}
        result["quant_skills"] = [p.parent.name for p in (settings.root / "research-skills").glob("*/SKILL.md")]
        if args.network:
            service = DataService(settings)
            if settings.enable_miaoxiang:
                result["mcp_tools"] = await service.mx.list_tools()
            if settings.deepseek_key:
                async with httpx.AsyncClient(timeout=25) as client:
                    response = await client.get(settings.deepseek_url + "/models", headers={"Authorization": "Bearer " + settings.deepseek_key})
                result["deepseek_models_status"] = response.status_code
                if response.status_code == 200:
                    result["deepseek_models"] = [x["id"] for x in response.json().get("data", [])]
        return result
    raise DataError("INVALID_COMMAND", "未知命令。")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="本地 DeepSeek A股投资研究 Agent")
    commands = parser.add_subparsers(dest="command", required=True)
    enriched=commands.add_parser("enrich",help="补齐真实日历、分红检查和时点正确的行业映射，不使用Choice")
    enriched.add_argument("dataset_id")
    limits=commands.add_parser("enrich-limits",help="用妙想补齐真实历史涨跌停价，不使用Choice")
    limits.add_argument("dataset_id")
    history = commands.add_parser("history",help="保存经校验的OHLCV快照，最多5只/365日")
    history.add_argument("codes",nargs="+")
    history.add_argument("--start",required=True)
    history.add_argument("--end",required=True)
    history.add_argument("--source",choices=["auto","public","miaoxiang","choice"],default="auto")
    quant = commands.add_parser("quant",help="本地量化工具；options为JSON参数对象")
    quant.add_argument("operation",choices=["catalog","factors","backtest","portfolio","plan","daily","predict","review","create","trade"])
    quant.add_argument("--options",default="{}")
    imported = commands.add_parser("import-csv")
    imported.add_argument("path")
    imported.add_argument("--source",required=True)
    imported.add_argument("--adjustment",choices=["raw","forward","backward"],required=True)
    imported.add_argument("--synthetic",action="store_true")
    ask = commands.add_parser("ask")
    ask.add_argument("question")
    quote = commands.add_parser("quote")
    quote.add_argument("code")
    quote.add_argument("--source", choices=["auto", "miaoxiang", "choice", "public"], default="auto")
    skill = commands.add_parser("skill")
    skill.add_argument("name", choices=["stock-fundamental-analysis", "mx-stocks-screener"])
    skill.add_argument("query")
    skill.add_argument("--stock-name", default="")
    commands.add_parser("screen")
    compare = commands.add_parser("compare")
    compare.add_argument("code")
    compare.add_argument("--sources", nargs="+", default=["miaoxiang", "public", "choice"])
    doctor = commands.add_parser("doctor")
    doctor.add_argument("--network", action="store_true")
    choice = commands.add_parser("choice-check")
    choice.add_argument("--data", action="store_true")
    query = commands.add_parser("choice-query")
    query.add_argument("operation", choices=["quote", "fundamentals", "history", "calendar", "news", "screen", "report", "macro", "usage"])
    query.add_argument("--code", default="600519.SH")
    query.add_argument("--start", default="")
    query.add_argument("--end", default="")
    query.add_argument("--report-date", default="")
    query.add_argument("--indicator", default="")
    search = commands.add_parser("choice-edb-search")
    search.add_argument("query")
    args = parser.parse_args()
    try:
        result = asyncio.run(execute(args, Settings.load()))
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    except Exception as exc:
        print(json.dumps(failed("agent", exc).dict(), ensure_ascii=False))
        sys.exit(1)


if __name__ == "__main__":
    main()
