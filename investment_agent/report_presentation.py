"""Readable reports and provenance links; never invent an external source URL."""
from __future__ import annotations

import json
from pathlib import Path
import re
from urllib.parse import urlencode, urlsplit

BASE_URL = 'http://127.0.0.1:8501/'
RUN_PATTERN = r'\d{4}-\d{2}-\d{2}-[a-f0-9]{8}'
STAGE_LABELS = [('fundamentals','基本面与估值'),('technicals','技术与量价'),
                ('events','公告与政策'),('bull','支持观点'),('bear','反对观点与风险')]
STATUS_LABELS = {'available':'已取得','available_unverified_publication':'已取得，披露日待核对',
    'no_candidates':'筛选完成，暂无合格候选',
    'available_partial_publication_verified':'已取得，部分披露日已核对','unavailable':'未取得',
    'body_retrieved':'已读取原文','checked':'已核对','completed':'已完成','partial':'部分完成',
    'date_conflict':'日期冲突，已隔离','identity_unverified':'证券身份待核对'}


def load_packet(root: Path, run_id: str) -> dict | None:
    if not re.fullmatch(RUN_PATTERN,str(run_id)): return None
    directory=(root/'data/ashare/runs').resolve()
    path=(directory/run_id/'packet.json').resolve()
    if not path.is_relative_to(directory) or not path.is_file(): return None
    try: return json.loads(path.read_text(encoding='utf-8'))
    except (ValueError,OSError): return None


def local_link(**params) -> str:
    return BASE_URL+'?'+urlencode(params)


def public_url(url) -> str | None:
    if not isinstance(url,str): return None
    try:
        parts=urlsplit(url)
        if parts.scheme not in ('http','https') or not parts.hostname or parts.username or parts.password: return None
        if any(ord(c)<32 for c in url): return None
        if re.search(r'token|api.?key|password|authorization|signature',parts.query,re.I): return None
        return url.replace(' ','%20').replace('(','%28').replace(')','%29')
    except ValueError: return None


def markdown_link(label: str, url: str) -> str:
    label=re.sub(r'[\[\]<>\n|]',' ',str(label)).strip()
    return f'[{label}]({url})'


def evidence_links(packet: dict) -> dict[str,tuple[str,str]]:
    links={}
    for item in packet.get('evidence',[]):
        data=item.get('data',{})
        subject=item.get('subject','来源')
        kind=item.get('kind')
        url=public_url(data.get('url'))
        if kind=='financial_reconciliation': label=subject+'财报原文'
        elif kind=='technical': label=subject+'行情与指标'
        elif kind=='fundamental': label=subject+'财务数据'
        elif kind=='official_disclosures': label='公司公告索引'
        elif kind=='official_recent_announcements': label='近期公司公告'
        elif kind=='sector_screening': label='完整行业筛选记录'
        elif kind=='news': label='新闻检索结果'
        elif kind=='notice': label='公告检索结果'
        else: label=subject
        if kind=='document' and item.get('source')=='official_disclosure' and re.search('[:：]',subject):
            issuer=re.split('[:：]',subject)[0]
            year=re.search(r'20\d{2}',subject)
            report_type='半年报' if '半年度' in subject else '年报' if '年度报告' in subject else '财报'
            label=issuer+(year[0] if year else '')+report_type
        # Multi-source/derived evidence opens its exact local record. A domain
        # homepage or another company's report would misrepresent provenance.
        if not url: url=local_link(research=packet['run_id'],evidence=item['id'])
        if kind=='document' and item.get('status')!='body_retrieved': label+='（原文未取回）'
        links[item['id']]=(label,url)
    return links


def normalize_markdown(text: str) -> str:
    lines=str(text).replace('\r\n','\n').split('\n')
    output=[]
    fenced=False
    for index,line in enumerate(lines):
        if line.lstrip().startswith('```'): fenced=not fenced
        if not fenced and not line.strip() and output and output[-1].strip().startswith('|'):
            following=next((x.strip() for x in lines[index+1:] if x.strip()),'')
            if following.startswith('|'): continue
        output.append(line)
    return '\n'.join(output).strip()


def linked_text(text: str, links: dict) -> str:
    def replace(match):
        content=match[1]
        ids=re.findall(r'(?<![A-Za-z0-9])([ES]\d+)(?!\d)',content)
        if not ids: return match[0]
        rendered=[]
        for key in dict.fromkeys(ids):
            item=links.get(key)
            rendered.append(markdown_link(*item) if item else '来源待核验')
        note=re.sub(r'[ES]\d+','',content).strip(' ,，、/;； ')
        suffix=f'（{note}）' if note and re.search(r'[\u4e00-\u9fff]',note) else ''
        return '（'+' · '.join(dict.fromkeys(rendered))+'）'+suffix
    # Existing Markdown hyperlinks remain untouched; grouped citations work too.
    text=re.sub(r'[\[【]([^\]\n】]+)[\]】](?!\()',replace,text)
    # Some old reports mention E16 in running prose. Preserve URLs and code
    # while linking those references as well.
    parts=re.split(r'(\[[^\]\n]+\]\([^\s\n]+\)|`[^`\n]+`)',text)
    for index in range(0,len(parts),2):
        parts[index]=re.sub(r'(?<![A-Za-z0-9])([ES]\d+)(?![A-Za-z0-9])',
            lambda m:markdown_link(*links[m[1]]) if m[1] in links else '来源待核验',parts[index])
    text=''.join(parts)
    return normalize_markdown(text)


def review_text(text: str, packet: dict) -> str:
    text=linked_text(text,evidence_links(packet))
    for asset in packet.get('universe',[]):
        name=re.escape(asset['name'])
        text=re.sub(r'(?m)^\s*\*\*('+name+r'(?:[：:][^*\n]{0,60})?)\*\*\s*[：:]?\s*(?=\n|$)',r'\n\n### \1\n\n',text)
    for label in ('支持因素','支持理由','反对因素','反对理由','观察触发条件','触发条件','失效条件','解除回避条件','下一次复核','技术参考位'):
        text=re.sub(r'(?<![\u4e00-\u9fff])(?:\*\*)?'+label+r'(?:\*\*)?[:：](?:\*\*)?',r'\n\n**'+label+'：** ',text)
    return text.strip()


def coverage_rows(packet: dict) -> list[dict]:
    stocks=list(dict.fromkeys([a['name'] for a in packet.get('universe',[])]+[r['stock'] for r in packet.get('statuses',[])]))
    result=[]
    for name in stocks:
        dimensions={r['dimension']:r for r in packet.get('statuses',[]) if r['stock']==name}
        fundamental=next((e['data'] for e in packet.get('evidence',[]) if e['kind']=='fundamental' and e['subject']==name),{})
        periods=fundamental.get('periods') or []
        technical=dimensions.get('technical',{})
        financial=dimensions.get('fundamental',{})
        result.append({'股票':name,'行情日期':technical.get('as_of','未取得'),
            '技术数据':STATUS_LABELS.get(technical.get('status'),'未取得'),
            '财报期间':periods[0]['period'] if periods else '未取得',
            '财务数据':'已取得，部分核对' if financial.get('status')=='available_partial_publication_verified' else STATUS_LABELS.get(financial.get('status'),'未取得')})
    return result


def coverage_markdown(packet: dict) -> str:
    rows=coverage_rows(packet)
    if not rows: return '本次没有可展示的数据覆盖记录。'
    columns=list(rows[0])
    def row(values): return '| '+' | '.join(str(x).replace('|','／').replace('\n',' ') for x in values)+' |'
    return '\n'.join([row(columns),row(['---']*len(columns)),*[row(r.values()) for r in rows]])


def source_markdown(packet: dict) -> str:
    links=evidence_links(packet)
    lines=[]
    for item in packet.get('evidence',[]):
        status=STATUS_LABELS.get(item['status'],item['status'])
        lines.append('- '+markdown_link(*links[item['id']])+' — '+status)
    return '\n'.join(lines)


def report_markdown(packet: dict, result: dict) -> str:
    links=evidence_links(packet)
    blocks=['# '+packet['topic']+'研究报告',
        f"研究截止：{packet['cutoff']} · {STATUS_LABELS.get(result['status'],result['status'])}",
        '## 结论与建议',review_text(result.get('reports',{}).get('review','最终建议阶段尚未完成，请先查看已取得的资料与缺口。'),packet),
        '## 数据覆盖',coverage_markdown(packet)]
    if packet.get('screening'):
        blocks.insert(2,screening_markdown(packet['screening']))
    if any(r['status']=='unavailable' for r in packet.get('statuses',[])):
        blocks.append('**数据缺口：部分财务或技术资料未取得，不能将本报告视为完整的投资判断。**')
    blocks.append(packet.get('sample_note',''))
    for key,label in STAGE_LABELS:
        if key in result.get('reports',{}): blocks += ['## '+label,linked_text(result['reports'][key],links)]
    if result.get('error'): blocks+=['## 未完成原因',str(result['error'].get('message',result['error']))]
    blocks+=['## 来源与证据',source_markdown(packet),'## 阅读说明',
        '财报披露日按逐项核验结果使用；引用可以追溯来源，但不代表全部数字和模型推断均已核验。'
        '本地指标链接打开本次计算证据，原文链接打开对应发布页面。技术收益不等于策略收益。',
        f"模型：{result.get('model','未知')} · 推理：{result.get('reasoning_effort','未知')} · 生成：{result.get('created_at','')} · 记录：{packet['run_id']}"]
    return '\n\n'.join(blocks)


def screening_rows(audit):
    return [{'股票':r['name']+' '+r['code'][:6],'子行业':r['segment'],'评分':r['score'],
             '财报期间':r['period'] or '未取到','行情日期':r['as_of'] or '未确认',
             '结果':'深入研究' if r['code'] in audit['selected_codes'] else '通过初筛' if r['eligible'] else '排除',
             '原因':'；'.join(r['reasons']) or '按所选风格的池内评分排序'} for r in audit['rows']]


def screening_markdown(audit):
    profile=audit['profile']
    lines=['## 行业筛选过程',f"偏好：{profile['label']}（{profile['horizon']}）。覆盖 {audit['candidate_count']} 只，取得财报 {audit['financial_coverage']} 只，通过门槛 {audit['eligible_count']} 只，深入研究 {len(audit['selected_codes'])} 只。",
           '| 股票 | 评分 | 结果 | 原因 |','| --- | --- | --- | --- |']
    for r in screening_rows(audit)[:20]:
        lines.append('| '+' | '.join(str(r[k]).replace('|','／') for k in ('股票','评分','结果','原因'))+' |')
    lines+=['',audit['formula'],'；'.join(audit['limitations'])]
    return '\n'.join(lines)


def chat_links(output: dict, root: Path) -> dict:
    links={}
    report_id=Path(output.get('report_file','')).stem
    for item in output.get('trace',[]):
        result=item.get('result',{})
        data=result.get('data') or {}
        run_id=data.get('run_id') if isinstance(data,dict) else None
        packet=load_packet(root,run_id) if run_id else None
        if packet:
            links.update(evidence_links(packet))
            links[item['id']]=('查看这份研究报告',local_link(research=run_id))
        elif isinstance(data,dict) and public_url(data.get('url')):
            links[item['id']]=(data.get('title') or '原始资料',public_url(data['url']))
        elif re.fullmatch(r'[a-f0-9]{32}',report_id):
            links[item['id']]=('查看本次数据来源',local_link(report=report_id,source=item['id']))
    return links
