import asyncio
from copy import deepcopy
from datetime import datetime, timedelta
import hashlib
import json
import threading
import time
from typing import Awaitable, Callable

import duckdb

from .choice import ChoiceProvider
from .common import DataError, Result, failed, number, safe, symbol, TZ
from .config import Settings
from .miaoxiang import MiaoxiangProvider
from .public import PublicDataProvider

CACHE_LOCK = threading.RLock()


class Cache:
    def __init__(self, path):
        self.path = str(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with CACHE_LOCK, duckdb.connect(self.path) as db:
            db.execute("CREATE TABLE IF NOT EXISTS cache (k VARCHAR PRIMARY KEY, expires DOUBLE, value VARCHAR)")

    def get(self, key: str) -> Result | None:
        with CACHE_LOCK, duckdb.connect(self.path) as db:
            row = db.execute("SELECT value FROM cache WHERE k=? AND expires>?", [key, time.time()]).fetchone()
        if row:
            result = Result(**json.loads(row[0]))
            result.cached = True
            return result
        return None

    def put(self, key: str, result: Result, ttl: int) -> None:
        with CACHE_LOCK, duckdb.connect(self.path) as db:
            db.execute("INSERT OR REPLACE INTO cache VALUES (?,?,?)", [key, time.time() + ttl,
                json.dumps(result.dict(), ensure_ascii=False, default=str, allow_nan=False)])


class DataService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.mx = MiaoxiangProvider(settings)
        self.choice = ChoiceProvider(settings)
        self.public = PublicDataProvider(settings)
        self.cache = Cache(settings.root / "data/cache.duckdb")
        self.calls = 0
        self.errors: list[dict] = []
        self.blocked: set[str] = set()
        self.results: list[Result] = []
        self.failed_requests: dict[str, Result] = {}
        from .research_runtime import ResearchStore
        self.runtime_store=ResearchStore(settings.root)

    def circuit_key(self,source):
        credential=self.settings.mcp_key+'|'+self.settings.skills_key if source=='miaoxiang' else source
        return source+':'+hashlib.sha256(credential.encode()).hexdigest()

    def enabled(self, source: str) -> bool:
        return {"miaoxiang": self.settings.enable_miaoxiang, "choice": self.settings.enable_choice,
                "public": self.settings.enable_public}[source]

    async def fetch(self, source: str, args: dict, action: Callable[[], Awaitable[Result]], ttl=60) -> Result:
        from .research_runtime import ACTIVE_BUDGET
        budget=ACTIVE_BUDGET.get()
        if budget and budget.cancelled and budget.cancelled():
            raise DataError('TASK_CANCELLED','已停止后续数据请求。')
        if not self.enabled(source):
            return failed(source, DataError("DISABLED", source + " 已关闭；未请求该来源。"))
        if source=='miaoxiang':
            with self.runtime_store.connect() as db:
                circuit=db.execute('SELECT reason FROM circuits WHERE key=?',(self.circuit_key(source),)).fetchone()
            if circuit: return failed(source,DataError('SOURCE_CIRCUIT_OPEN','妙想此前额度或权限失败，已暂停；请在网页来源设置中确认恢复后重试。'))
        if source in self.blocked:
            return failed(source, DataError("SOURCE_BLOCKED", "该来源本轮已出现权限错误，停止重复调用。"))
        credential = self.settings.mcp_key if source == "miaoxiang" else ""
        key = hashlib.sha256(json.dumps([source, args, hashlib.sha256(credential.encode()).hexdigest()],
                                       sort_keys=True, ensure_ascii=True).encode()).hexdigest()
        if key in self.failed_requests:
            result = deepcopy(self.failed_requests[key])
            result.metadata["duplicate_failure_suppressed"] = True
            return result
        cached = self.cache.get(key)
        if cached:
            if source=='miaoxiang':
                from .miaoxiang import check_business_error
                try: check_business_error(cached.data)
                except DataError as exc:
                    self.blocked.add(source)
                    return failed(source,exc)
            self.results.append(cached)
            return cached
        if self.calls >= self.settings.max_calls:
            return failed(source, DataError("BUDGET_EXHAUSTED", "本轮数据调用预算已用完。"))
        self.calls += 1
        try:
            result = await action()
            result.data = safe(result.data, (self.settings.mcp_key, self.settings.skills_key, self.settings.deepseek_key))
            self.cache.put(key, result, ttl)
            self.results.append(result)
            return result
        except Exception as exc:
            result = failed(source, exc)
            self.errors.append(result.dict())
            self.failed_requests[key] = result
            def rejected(e: Exception) -> bool:
                response = getattr(e, "response", None)
                return getattr(response, "status_code", None) in {401, 403, 429} or any(
                    rejected(child) for child in getattr(e, "exceptions", []))
            if isinstance(exc, DataError) and ("AUTH" in exc.code or "QUOTA" in exc.code or exc.code.startswith("CHOICE_10001")):
                self.blocked.add(source)
            elif rejected(exc):
                self.blocked.add(source)
            if source=='miaoxiang' and source in self.blocked:
                with self.runtime_store.connect() as db:
                    db.execute('INSERT OR REPLACE INTO circuits VALUES(?,?,?)',(self.circuit_key(source),getattr(exc,'code',type(exc).__name__),time.time()))
            return result

    async def stock(self, code: str, kind="quote", source="auto", query="", start="", end="") -> Result:
        code = symbol(code)
        if kind not in {"quote", "fundamentals", "history"}:
            raise DataError("INVALID_KIND", "仅支持行情、基本面或历史数据。")
        if source not in {"auto", "miaoxiang", "choice", "public"}:
            raise DataError("INVALID_SOURCE", "未知数据来源。")
        sources = [source] if source != "auto" else [s for s in ("public", "miaoxiang", "choice") if self.enabled(s)]
        if not sources:
            return failed("none", DataError("NO_SOURCE", "所有数据源均关闭。"))
        attempts = []
        for selected in sources:
            args = {"code": code, "kind": kind, "query": query, "start": start, "end": end}
            if selected == "miaoxiang":
                detail = query or {"quote": "最新价、涨跌幅与准确行情时间", "fundamentals": "最新财报报告期、ROE、现金流、PE(TTM)、PB与单位",
                                   "history": f"{start or '最近5个交易日'}至{end or '今天'}的日线收盘价与日期，不复权"}[kind]
                result = await self.fetch(selected, args, lambda: self.mx.mcp("mx_ashare_finance_data",
                    f"仅查询A股 {code} 的{detail}。不要扩展到其他股票。明确日期、口径与单位。", code, kind), 45 if kind == "quote" else 900)
            elif selected == "choice":
                result = await self.fetch(selected, args, lambda: asyncio.to_thread(self.choice.request, kind, code, start, end))
            else:
                result = await self.fetch(selected, args, lambda: self.public.request(code, kind, start, end))
            if result.ok:
                if attempts:
                    result.metadata["fallback_attempts"] = attempts
                return result
            attempts.append({"source": selected, "error": result.error})
        result.metadata["attempts"] = attempts
        return result

    async def skill(self, name: str, query: str, stock_name: str = "", limit: int = 5) -> Result:
        if name == "mx-stocks-screener":
            query += f"；最多返回{max(1,min(150,limit))}只A股，不查询其他品种。"
        return await self.fetch("miaoxiang", {"skill": name, "query": query, "stock_name": stock_name,
            "credential": hashlib.sha256(self.settings.skills_key.encode()).hexdigest()},
            lambda: self.mx.skill(name, query, stock_name), ttl=900)

    async def news(self, query: str, notices=False, code=None, name=None) -> Result:
        tool = "mx_finance_search_notice" if notices else "mx_finance_search_news"
        attempts=[]
        if self.settings.enable_miaoxiang:
            remote=await self.fetch("miaoxiang", {"tool": tool, "query": query}, lambda: self.mx.mcp(tool, query), 600)
            if remote.ok and remote.data: return remote
            attempts.append({'source':'miaoxiang','error':remote.error})
        from .intelligence.service import IntelligenceService
        from .china_research import discover_official
        local=IntelligenceService(self.settings.root).search_intelligence(name or query)
        official=await discover_official(self,query,'governance' if notices else 'business-industry',code=code,name=name)
        records=list(official['evidence'])
        for event in local['data']:
            records.extend({'title':a['title'],'url':a['url'],'published_at':a['published'],
                            'coverage':a['coverage'],'publisher':'本地情报原始来源'} for a in event['sources'])
        unique={r['url']:r for r in records if r.get('url')}
        if not unique:
            return Result(source='public_news',ok=False,error={'code':'PUBLIC_NEWS_EMPTY','message':'本轮公开入口与本地情报未取得匹配材料；不表示公司没有公告或事件。'},
                          metadata={'attempts':attempts,'requests':official['requests'],'local_coverage':local['coverage']})
        table={'columns':['标题','跳转链接','发布时间','摘要','来源','检索入口'],
               'items':[[r.get('title'),r['url'],r.get('published_at'),r.get('body') or r.get('summary') or r.get('coverage','仅索引'),
                         r.get('publisher','官方披露'),'public_discovery'] for r in unique.values()]}
        return Result(source='public_news',data={'tables':[table],'coverage':'有限官方入口与本地情报，非全网；原文缺失或日期未知必须继续核验。'},
                      metadata={'attempts':attempts,'requests':official['requests']})

    async def compare(self, code: str, sources: list[str]) -> dict:
        if len(sources) < 2 or len(sources) > 3 or len(set(sources)) != len(sources):
            raise DataError("INVALID_SOURCES", "请选择两个或三个不同来源。")
        results = [await self.stock(code, source=s) for s in sources]
        return compare_quotes(results)


def compare_quotes(results: list[Result]) -> dict:
    quotes = []
    for r in results:
        if not r.ok:
            continue
        q = r.metadata.get("normalized_quote")
        if not q and isinstance(r.data, dict) and "price" in r.data:
            q = {**r.data, "data_time": r.data_time}
        if q:
            quotes.append({"source": r.source, **q})
    checks = []
    for a, b in zip(quotes, quotes[1:]):
        same = all(a.get(k) is not None and a.get(k) == b.get(k) for k in ("data_time", "unit", "currency", "price_basis"))
        checks.append({"sources": [a["source"], b["source"]], "comparable": same,
                       "observed_price_difference": round(a["price"] - b["price"], 8),
                       "conflict": same and a["price"] != b["price"],
                       "note": "仅同时间/单位/口径可判定冲突；不同时间不直接比较。"})
    return {"results": [r.dict() for r in results], "comparisons": checks,
            "independence_note": "妙想与AKShare东财公开行情可能具有相同原始来源。"}


def screening_query(plan: dict) -> tuple[str, list[int]]:
    year = datetime.now(TZ).year
    years = list(range(year - 3, year))
    roe = float(plan.get("min_roe_pct", 10))
    pe = float(plan.get("max_pe_ttm", 30))
    amount = float(plan.get("min_daily_amount_cny", 1e8))
    limit = int(plan.get("limit", 5))
    if not (0 <= roe <= 100 and 1 <= pe <= 200 and 0 <= amount <= 1e12 and 1 <= limit <= 5):
        raise DataError("INVALID_SCREEN", "选股参数超出本地限额。")
    trade_date = completed_session_candidate()
    text = (f"筛选A股，仅返回前{limit}只，剔除ST；{years}各年年报加权ROE均不低于{roe}%；"
            f"最新PE(TTM)>0且不超过{pe}倍，{trade_date}当日成交额不低于{amount}元；按该日成交额降序。"
            f"请逐行返回证券代码、名称、各年ROE(%)、PE(TTM)、成交额(元)、指标对应日期。")
    return text, years


def completed_session_candidate() -> str:
    current = datetime.now(TZ)
    day = current.date()
    if (current.hour, current.minute) < (15, 0):
        day -= timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    # Holidays cannot be inferred from weekdays: missing this exact date remains unverified.
    return day.isoformat()


def verify_screen_rows(rows: list[dict], plan: dict, years: list[int]) -> list[dict]:
    import re
    output = []
    for row in rows[:5]:
        checks = {}
        for y in years:
            keys = [k for k in row if str(y) in k and ("roe" in k.lower() or "净资产收益率" in k)]
            annual_keys = [k for k in keys if re.search(fr"{y}[.-]12[.-]31", k) or f"{y}年报" in str(row[k])]
            if annual_keys:
                keys = annual_keys
            elif "trade_date" in plan:
                keys = []
            v = number(row[keys[0]]) if len(keys) == 1 and ("%" in keys[0] or "%" in str(row[keys[0]])) else None
            checks[f"roe_{y}"] = None if v is None else v >= plan.get("min_roe_pct", 10)
        keys = [k for k in row if ("pe" in k.lower() or "市盈率" in k) and "ttm" in k.lower()]
        pe = number(row[keys[0]]) if len(keys) == 1 else None
        keys = [k for k in row if "成交额" in k and "元" in k]
        amount = number(row[keys[0]]) if len(keys) == 1 else None
        if amount is not None and "亿" in keys[0] and "亿" not in str(row[keys[0]]):
            amount *= 1e8
        elif amount is not None and "万" in keys[0] and "万" not in str(row[keys[0]]):
            amount *= 1e4
        checks["pe_ttm"] = None if pe is None else 0 < pe <= plan.get("max_pe_ttm", 30)
        checks["liquidity"] = None if amount is None else amount >= plan.get("min_daily_amount_cny", 1e8)
        if "trade_date" in plan:
            date_matches = re.findall(r"\d{4}[.-]\d{2}[.-]\d{2}", keys[0]) if len(keys) == 1 else []
            checks["liquidity_date"] = None if not date_matches else (len(date_matches) == 1 and date_matches[0].replace(".", "-") == plan["trade_date"])
            st_value = next((str(v).strip() for k, v in row.items() if k == "ST股票"), None)
            checks["not_st"] = None if st_value is None else st_value in {"否", "0", "False", "false"}
        status = "failed" if False in checks.values() else "unverified" if None in checks.values() else "verified"
        output.append({"row": deepcopy(row), "checks": checks, "status": status})
    return output
