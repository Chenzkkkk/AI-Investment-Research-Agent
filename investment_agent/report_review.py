"""Apply bounded, evidence-linked editorial corrections without rewriting a report."""
import json
import re
from .common import DataError


def review_blocks(report, limit=4500):
    """Keep paragraphs/table rows intact and explicitly bound the review scope."""
    blocks=[]; current=[]; size=0
    for line in report.splitlines(keepends=True):
        if size+len(line)>limit and current:
            blocks.append(''.join(current));current=[];size=0
        if len(line)>limit:
            raise DataError('REPORT_REVIEW_SIZE','报告包含过长段落，需拆分后再审阅。')
        current.append(line);size+=len(line)
    if current:blocks.append(''.join(current))
    if len(blocks)>4:raise DataError('REPORT_REVIEW_SIZE','报告超出本轮分段审阅范围，未宣称审阅完成。')
    return blocks


def apply_review(report,text,trace):
    try:
        value=json.loads(re.sub(r'^```(?:json)?\s*|\s*```$','',text.strip()))
    except (TypeError,ValueError):
        raise DataError('REPORT_REVIEW_FORMAT','证据审阅未返回有效的结构化检查记录。')
    known={t['id'] for t in trace if t.get('result',{}).get('ok')}
    refs=value.get('reviewed_evidence') if isinstance(value,dict) else None
    if not isinstance(refs,list) or not refs or any(r not in known for r in refs):
        raise DataError('REPORT_REVIEW_EVIDENCE','审阅记录缺少有效的已执行证据。')
    corrections=value.get('corrections')
    if not isinstance(corrections,list) or len(corrections)>8:
        raise DataError('REPORT_REVIEW_FORMAT','审阅修正记录数量或结构不正确。')
    if not isinstance(value.get('summary'),str) or not re.search(r'[\u4e00-\u9fff]',value['summary']):
        raise DataError('REPORT_REVIEW_FORMAT','审阅摘要需要中文。')
    from .reader_evidence import originals
    allowed_urls={s['url'] for s in originals(trace)}|set(re.findall(r'https?://[^\s)]+',report))
    edits=[]
    for edit in corrections:
        if not isinstance(edit,dict):raise DataError('REPORT_REVIEW_FORMAT','修正记录格式不正确。')
        old,new=edit.get('original'),edit.get('replacement')
        ids=edit.get('evidence_ids')
        if not isinstance(old,str) or not old or report.count(old)!=1 or not isinstance(new,str):
            raise DataError('REPORT_REVIEW_SPAN','修正未唯一匹配原文；原报告保持不变。')
        if not isinstance(ids,list) or not ids or any(i not in known for i in ids):
            raise DataError('REPORT_REVIEW_EVIDENCE','修正引用了不存在或失败的证据。')
        if any(u not in allowed_urls for u in re.findall(r'https?://[^\s)]+',new)):
            raise DataError('REPORT_REVIEW_URL','修正含未取得的新来源，未自动采信。')
        if any(i not in known for i in re.findall(r'\[(S\d+)\]',new)):
            raise DataError('REPORT_REVIEW_EVIDENCE','修正含不存在的引用。')
        start=report.index(old);end=start+len(old)
        if any(start<b and end>a for a,b,_ in edits):
            raise DataError('REPORT_REVIEW_SPAN','修正范围重叠，未自动覆盖。')
        edits.append((start,end,new))
    for start,end,new in sorted(edits,reverse=True):report=report[:start]+new+report[end:]
    return report,value
