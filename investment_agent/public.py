import asyncio
import json
import os
import sys
import httpx
from .common import DataError, Result
from .config import Settings


class PublicDataProvider:
    def __init__(self, settings: Settings):
        self.settings = settings

    async def request(self, code: str, kind: str = "quote", start: str = "", end: str = "", codes=None) -> Result:
        if not self.settings.enable_public:
            raise DataError("DISABLED", "公开数据源已关闭。")
        if kind not in {"quote", "history", "fundamentals", "research_metadata"}:
            raise DataError("UNSUPPORTED", "公开数据适配器支持行情、日线与估值。")
        public_error=None
        if kind=='history':
            try:
                return await self.eastmoney_history(code,start,end)
            except Exception as exc:
                public_error=getattr(exc,'code',type(exc).__name__)
            try:
                backup=await self.tencent_history(code,start,end)
                backup.metadata['eastmoney_fallback_reason']=public_error
                return backup
            except Exception as exc:
                public_error+='; tencent:'+getattr(exc,'code',type(exc).__name__)
        env = {k: v for k, v in os.environ.items() if not any(x in k.upper() for x in ("API_KEY", "PASSWORD", "TOKEN"))}
        env.update({"PYTHONUTF8": "0", "PYTHONIOENCODING": "utf-8"})
        process = await asyncio.create_subprocess_exec(sys.executable, "-X", "utf8=0",
            str(self.settings.root / "scripts/public_worker.py"), stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, cwd=str(self.settings.root), env=env)
        try:
            stdout, _ = await asyncio.wait_for(process.communicate(json.dumps(
                {"code": code, "codes":codes, "kind": kind, "start": start, "end": end}).encode()), timeout=35)
        except asyncio.TimeoutError:
            process.kill()
            await process.communicate()
            raise DataError("PUBLIC_TIMEOUT", "公开数据请求超时，未自动重试。") from None
        try:
            response = json.loads(stdout.decode("utf-8"))
        except (ValueError, UnicodeError):
            raise DataError("PUBLIC_PROTOCOL", "公开来源未返回有效结果。") from None
        if not response.get("ok"):
            raise DataError("PUBLIC_UNAVAILABLE", response.get("message", "公开数据源不可用。"))
        metadata=response.get('metadata',{})
        if public_error: metadata['eastmoney_fallback_reason']=public_error
        return Result(source=response["source"], symbol=code, kind=kind, data=response["data"],
                      data_time=response.get("data_time"), metadata=metadata)

    async def eastmoney_history(self,code,start,end):
        from datetime import date
        from .common import symbol
        code=symbol(code)
        begin,finish=date.fromisoformat(start),date.fromisoformat(end)
        if begin>finish or (finish-begin).days>365: raise DataError('HISTORY_RANGE','公开日线最多一年。')
        params={'secid':('1.' if code.endswith('.SH') else '0.')+code[:6],
            'fields1':'f1,f2,f3,f4,f5,f6','fields2':'f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61',
            'klt':'101','fqt':'0','beg':start.replace('-',''),'end':end.replace('-',''),'lmt':'400'}
        async with httpx.AsyncClient(timeout=20,trust_env=False) as client:
            response=await client.get('https://push2his.eastmoney.com/api/qt/stock/kline/get',params=params)
            response.raise_for_status(); data=response.json().get('data') or {}
        if data.get('code')!=code[:6] or not data.get('klines'):
            raise DataError('EASTMONEY_HISTORY_EMPTY','东方财富未返回目标股票日线。')
        rows=[]
        for line in data['klines']:
            values=line.split(',')
            if len(values)<7: raise DataError('EASTMONEY_HISTORY_SCHEMA','公开日线格式变化。')
            # The public feed volume is lots (手); our data contract is shares.
            stamp,op,close,high,low,volume,amount=values[:7]
            if start<=stamp<=end:
                rows.append({'symbol':code,'date':stamp,'open':float(op),'close':float(close),'high':float(high),
                             'low':float(low),'volume':float(volume)*100,'amount':float(amount)})
        if not rows: raise DataError('EASTMONEY_HISTORY_EMPTY','请求区间内没有有效日线。')
        return Result(source='eastmoney_public_kline',symbol=code,kind='history',data={'rows':rows},data_time=rows[-1]['date'],
            metadata={'url':str(response.url),'adjustment':'raw','units':{'prices':'CNY/share','volume':'shares','amount':'CNY'},
                      'volume_conversion':'feed lots * 100','origin':'东方财富公开网页日线，不是Choice付费API'})

    async def tencent_history(self,code,start,end):
        from .common import symbol
        code=symbol(code)
        if code.endswith('.BJ'): raise DataError('PUBLIC_MARKET','腾讯备用适配器未核实北交所成交量口径。')
        ticker=code[-2:].lower()+code[:6]
        async with httpx.AsyncClient(timeout=20,trust_env=False) as client:
            response=await client.get('https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get',
                params={'param':f'{ticker},day,{start},{end},400,'})
            response.raise_for_status(); data=(response.json().get('data') or {}).get(ticker) or {}
        bars=data.get('day')  # Never silently accept qfqday/hfqday for raw execution prices.
        if not bars: raise DataError('TENCENT_HISTORY_EMPTY','腾讯未返回目标股票不复权日线。')
        rows=[]
        scale=1 if code.startswith('688') else 100
        for values in bars:
            if len(values)<9: raise DataError('TENCENT_HISTORY_SCHEMA','腾讯日线格式变化。')
            stamp,op,close,high,low,volume=values[:6]
            if start<=stamp<=end:
                rows.append({'symbol':code,'date':stamp,'open':float(op),'close':float(close),'high':float(high),
                    'low':float(low),'volume':float(volume)*scale,'amount':float(values[8])*10000})
        if not rows: raise DataError('TENCENT_HISTORY_EMPTY','区间内无日线。')
        return Result(source='tencent_public_kline',symbol=code,kind='history',data={'rows':rows},data_time=rows[-1]['date'],
            metadata={'url':str(response.url),'adjustment':'raw','units':{'prices':'CNY/share','volume':'shares','amount':'CNY'},
                      'volume_scale':scale,'amount_scale':10000,'origin':'腾讯公开行情备用源，非东方财富数据'})
