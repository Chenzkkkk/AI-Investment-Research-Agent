from contextlib import redirect_stdout, redirect_stderr
from datetime import date, timedelta
import io
import json
import sys


def fetch(args: dict) -> dict:
    code = args["code"]
    if args["kind"] == "quote":
        import akshare as ak
        frame = ak.stock_bid_ask_em(symbol=code[:6])
        values = dict(zip(frame["item"], frame["value"]))
        price = float(values["最新"])
        if price <= 0:
            raise ValueError("No valid quote")
        return {"ok": True, "source": "public_akshare_eastmoney", "data": {"price": price,
                "price_basis": "最新价", "currency": "CNY", "unit": "元/股"},
                "data_time": None, "metadata": {"timestamp_warning": "该公开接口未提供行情时间，不能当作已核验实时价",
                "origin": "东方财富公开行情；与妙想不构成完全独立的原始来源"}}
    import baostock as bs
    finish = date.fromisoformat(args["end"]) if args.get("end") else date.today()
    begin = date.fromisoformat(args["start"]) if args.get("start") else finish - timedelta(days=14)
    if begin > finish or (finish - begin).days > 365:
        raise ValueError("History range exceeds one year")
    login = bs.login()
    if login.error_code != "0":
        raise RuntimeError("BaoStock login unavailable")
    try:
        if args["kind"] == "research_metadata":
            def records(result):
                if result.error_code != "0": raise RuntimeError("BaoStock metadata unavailable")
                rows=[]
                while result.next(): rows.append(dict(zip(result.fields,result.get_row_data())))
                return rows
            codes=args.get("codes") or [code]
            if not 1<=len(codes)<=5: raise ValueError("Metadata limited to five assets")
            calendar=records(bs.query_trade_dates(start_date=begin.isoformat(),end_date=finish.isoformat()))
            industries,dividends,errors,requests=[],[],[],1
            for asset in codes:
                bscode=asset[-2:].lower()+"."+asset[:6]
                requests+=1
                try: industries+=records(bs.query_stock_industry(code=bscode,date=finish.isoformat()))
                except Exception: errors.append({"symbol":asset,"kind":"industry","status":"unavailable"})
                for year in range(begin.year,finish.year+1):
                    requests+=1
                    try: dividends+=records(bs.query_dividend_data(bscode,year=str(year),yearType="operate"))
                    except Exception: errors.append({"symbol":asset,"kind":"dividend","year":year,"status":"unavailable"})
            return {"ok":True,"source":"public_baostock","data":{"calendar":calendar,"industries":industries,
                "dividends":dividends,"errors":errors,"subrequests":requests},"data_time":finish.isoformat(),
                "metadata":{"kind":"calendar_industry_corporate_actions","start":begin.isoformat(),"end":finish.isoformat()}}
        result = bs.query_history_k_data_plus(code[-2:].lower() + "." + code[:6],
            "date,code,open,high,low,close,preclose,volume,amount,tradestatus,isST,peTTM,pbMRQ", start_date=begin.isoformat(), end_date=finish.isoformat(),
            frequency="d", adjustflag="3")
        if result.error_code != "0":
            raise RuntimeError("BaoStock request failed")
        rows = []
        while result.next():
            rows.append(dict(zip(result.fields, result.get_row_data())))
        if not rows:
            raise ValueError("No public historical data")
        return {"ok": True, "source": "public_baostock", "data": {"rows": rows},
                "data_time": rows[-1]["date"], "metadata": {"price_basis": "历史日线收盘，不复权",
                "adjustment": "raw", "units": {"open": "元/股", "close": "元/股", "volume": "股", "amount": "元", "peTTM": "倍，TTM", "pbMRQ": "倍，MRQ"}}}
    finally:
        bs.logout()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    try:
        args = json.loads(sys.stdin.buffer.read().decode("utf-8"))
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            response = fetch(args)
        print(json.dumps(response, ensure_ascii=True, allow_nan=False))
    except Exception as exc:
        print(json.dumps({"ok": False, "message": "公开来源失败：" + type(exc).__name__}))
