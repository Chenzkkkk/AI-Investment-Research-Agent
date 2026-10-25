"""Offline, paginated Chinese PDF export with vector charts and clickable sources."""
from io import BytesIO
from html import escape
import math
import os
from pathlib import Path
from threading import RLock
from markdown_it import MarkdownIt
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
from reportlab.graphics.shapes import Drawing, String, Line, Rect
from .report_data import projections, financial_frame, source_links, title, external_url

_LOCK=RLock()
INK=colors.HexColor('#142b3b'); TEAL=colors.HexColor('#087e8b')


def font_name():
    if 'ZHChinese' in pdfmetrics.getRegisteredFontNames(): return 'ZHChinese'
    candidates=[Path(os.environ.get('WINDIR','C:/Windows'))/'Fonts/msyh.ttc',
        Path('/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc')]
    for p in candidates:
        if p.is_file():
            try:
                pdfmetrics.registerFont(TTFont('ZHChinese',str(p),subfontIndex=0))
                pdfmetrics.registerFontFamily('ZHChinese',normal='ZHChinese',bold='ZHChinese',italic='ZHChinese',boldItalic='ZHChinese')
                return 'ZHChinese'
            except Exception: continue
    if 'STSong-Light' not in pdfmetrics.getRegisteredFontNames(): pdfmetrics.registerFont(UnicodeCIDFont('STSong-Light'))
    pdfmetrics.registerFontFamily('STSong-Light',normal='STSong-Light',bold='STSong-Light',italic='STSong-Light',boldItalic='STSong-Light')
    return 'STSong-Light'


def inline(token):
    pieces=[]; links=[]
    for t in token.children or []:
        if t.type in ('text','code_inline','html_inline'): pieces.append(escape(t.content))
        elif t.type in ('softbreak','hardbreak'): pieces.append('<br/>')
        elif t.type=='strong_open': pieces.append('<b>')
        elif t.type=='strong_close': pieces.append('</b>')
        elif t.type=='em_open': pieces.append('<i>')
        elif t.type=='em_close': pieces.append('</i>')
        elif t.type=='link_open':
            url=external_url(t.attrGet('href'))
            links.append(bool(url))
            if url: pieces.append('<a href="'+escape(url,quote=True)+'" color="#087e8b">')
        elif t.type=='link_close':
            if links and links.pop(): pieces.append('</a>')
        elif t.type=='image': pieces.append(escape(t.content))
    return ''.join(pieces)


def markdown_flow(markdown, styles):
    tokens=MarkdownIt('commonmark',{'html':False}).enable('table').parse(markdown)
    story=[]; i=0; heading=0; in_list=0
    while i<len(tokens):
        t=tokens[i]
        if t.type=='heading_open': heading=int(t.tag[1])
        elif t.type=='heading_close': heading=0
        elif t.type in ('bullet_list_open','ordered_list_open'): in_list+=1
        elif t.type in ('bullet_list_close','ordered_list_close'): in_list-=1
        elif t.type=='table_open':
            rows=[]; row=[]; i+=1
            while i<len(tokens) and tokens[i].type!='table_close':
                if tokens[i].type=='tr_open': row=[]
                elif tokens[i].type=='inline': row.append(Paragraph(inline(tokens[i]),styles['cell']))
                elif tokens[i].type=='tr_close': rows.append(row)
                i+=1
            if rows:
                width=max(len(r) for r in rows)
                rows=[r+[Paragraph('',styles['cell'])]*(width-len(r)) for r in rows]
                table=Table(rows,colWidths=[483/width]*width,repeatRows=1,hAlign='LEFT',splitByRow=1,splitInRow=1)
                table.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#e8f2f3')),
                    ('VALIGN',(0,0),(-1,-1),'TOP'),('LINEBELOW',(0,0),(-1,-1),.3,colors.HexColor('#cfdae1')),
                    ('LEFTPADDING',(0,0),(-1,-1),6),('RIGHTPADDING',(0,0),(-1,-1),6),
                    ('TOPPADDING',(0,0),(-1,-1),7),('BOTTOMPADDING',(0,0),(-1,-1),7)]))
                story.extend([table,Spacer(1,12)])
        elif t.type=='inline':
            style=styles['h1' if heading==1 else 'h2'] if heading else styles['body']
            story.append(Paragraph(('• ' if in_list and not heading else '')+inline(t),style))
        elif t.type in ('fence','code_block','html_block'):
            story.append(Paragraph(escape(t.content).replace('\n','<br/>'),styles['cell']))
        elif t.type=='hr': story.append(Spacer(1,12))
        i+=1
    return story


def number(value):
    try:
        value=float(value)
        return value if math.isfinite(value) else None
    except (TypeError,ValueError): return None


def chart(title, frame, kind, font):
    """Vector drawings use the same observed rows as the interactive dashboard."""
    d=Drawing(483,230); d.add(String(4,214,title,fontName=font,fontSize=11,fillColor=INK))
    left,bottom,width,height=48,35,424,155
    if kind=='candles':
        frame=frame.tail(90).reset_index(drop=True)
        observations=[v for col in ('low','high') for v in frame[col] if number(v) is not None]
    else:
        frame=frame[frame['报告期'].str.endswith('12-31')].tail(5).reset_index(drop=True)
        observations=[number(v)/1e8 for col in ('营业收入','经营现金流','归母净利润') for v in frame[col] if number(v) is not None]
    if not observations or frame.empty: return None
    lo,hi=min(observations),max(observations)
    if kind!='candles': lo=min(0,lo); hi=max(0,hi)
    span=hi-lo or max(abs(hi)*.1,1); lo-=span*.08; hi+=span*.08
    y=lambda v:bottom+(v-lo)/(hi-lo)*height
    for tick in range(5):
        v=lo+(hi-lo)*tick/4; py=y(v)
        d.add(Line(left,py,left+width,py,strokeColor=colors.HexColor('#e2e8ed'),strokeWidth=.4))
        d.add(String(left-6,py-2,f'{v:.1f}',fontName=font,fontSize=7,textAnchor='end',fillColor=INK))
    step=width/max(len(frame),1)
    if kind=='candles':
        for idx,row in frame.iterrows():
            vals=[number(row[k]) for k in ('open','high','low','close')]
            if None in vals: continue
            o,h,l,c=vals; x=left+step*(idx+.5); color=colors.HexColor('#b54756' if c>=o else '#138575')
            d.add(Line(x,y(l),x,y(h),strokeColor=color,strokeWidth=.7))
            d.add(Rect(x-step*.32,y(min(o,c)),max(step*.64,.8),max(abs(y(c)-y(o)),.7),fillColor=color,strokeColor=color))
        for idx in sorted({0,len(frame)//2,len(frame)-1}):
            d.add(String(left+step*(idx+.5),20,str(frame.iloc[idx]['date'])[:10],fontName=font,fontSize=7,textAnchor='middle'))
    else:
        cols=['营业收入','经营现金流','归母净利润']; palette=['#224b62','#08a8a0','#ba9044']
        for idx,row in frame.iterrows():
            for j,col in enumerate(cols):
                value=number(row[col])
                if value is None: continue
                v=value/1e8; x=left+idx*step+step*(.12+j*.25)
                d.add(Rect(x,min(y(0),y(v)),step*.20,max(abs(y(v)-y(0)),.4),fillColor=colors.HexColor(palette[j]),strokeWidth=0))
            d.add(String(left+step*(idx+.5),20,str(row['报告期'])[:4],fontName=font,fontSize=8,textAnchor='middle'))
        for j,label in enumerate(cols):
            d.add(String(left+j*130,3,label,fontName=font,fontSize=8,fillColor=colors.HexColor(palette[j])))
    return d


def build_pdf(result, root, *, visual_data=None):
    # Font registration and ReportLab global registries are protected across users/threads.
    with _LOCK:
        return _build(result, root, visual_data)


def _build(result, root, visual_data):
    font=font_name(); out=BytesIO()
    styles={name:ParagraphStyle(name,fontName=font,fontSize=size,leading=leading,wordWrap='CJK',
        textColor=INK,spaceAfter=after,keepWithNext=name in ('h1','h2'))
        for name,size,leading,after in [('title',24,34,18),('h1',17,25,12),('h2',13,21,10),('body',10,17,8),('cell',8,13,5)]}
    doc=SimpleDocTemplate(out,pagesize=A4,leftMargin=56,rightMargin=56,topMargin=58,bottomMargin=52,
        title=str(result.get('question') or '知衡研究报告'),author='知衡研究台')
    story=[Paragraph('知衡 / INVESTMENT RESEARCH',styles['h2']),Spacer(1,24),
        Paragraph(escape(title(result)),styles['title']),
        Paragraph('研究问题：'+escape(str(result.get('question') or '未记录')),styles['body']),
        Paragraph('研究生成时间：'+escape(str(result.get('created_at','未记录'))),styles['body']),
        Paragraph('历史研究快照 · 数据日期以正文、财报期间与图表标注为准。观点、假设与事实分别核查；不代表收益承诺。',styles['body'])]
    data=visual_data if visual_data is not None else projections(result,root)
    if data.get('finances') or data.get('candles'):
        story.extend([Spacer(1,18),Paragraph('数据看板',styles['h1'])])
        for item in data.get('finances',[]):
            fig=chart(item['label']+' · 年度财务 / 亿元',financial_frame(item),'finance',font)
            if fig: story.append(fig)
            if item.get('coverage_note'): story.append(Paragraph(escape(str(item['coverage_note'])),styles['cell']))
        for item in data.get('candles',[]):
            fig=chart(item['label']+' · 日 K 线 / 元',item['frame'],'candles',font)
            if fig: story.append(fig)
            meta=item['meta']
            story.append(Paragraph(escape(f"来源 {meta.get('source','见证据')} · 复权 {meta.get('adjustment','未标注')} · 截止 {meta.get('end','未标注')} · 红涨绿跌，不含策略收益"),styles['cell']))
    for warning in data.get('warnings',[]): story.append(Paragraph(escape(warning),styles['body']))
    story.extend([PageBreak(),Paragraph('研究分析与判断',styles['h1'])])
    story.extend(markdown_flow(result.get('report','暂无正文'),styles))
    if data.get('finances'):
        story.extend([PageBreak(),Paragraph('财务数据明细',styles['h1'])])
        story.append(Paragraph('金额单位：亿元。完整年度与累计中期分别列示，不作年化；未取得的值保留为空。',styles['body']))
        for item in data['finances']:
            story.append(Paragraph(escape(item['label']),styles['h2']))
            frame=financial_frame(item)
            header='|报告期|营业收入|经营现金流|归母净利润|\n|---|---|---|---|\n'
            for row in frame.to_dict('records'):
                values=[('未取得' if number(row[col]) is None else f'{number(row[col])/1e8:,.3f}') for col in ('营业收入','经营现金流','归母净利润')]
                header+='|'+str(row['报告期'])+'|'+'|'.join(values)+'|\n'
            story.extend(markdown_flow(header,styles))
    for model in data.get('models',[]):
        story.append(Paragraph('三表预测 · 显式假设情景',styles['h2']))
        # Preserve model inputs and all rows without publishing raw tool logs.
        import json
        story.append(Paragraph(escape(json.dumps(model,ensure_ascii=False,indent=2)).replace('\n','<br/>'),styles['cell']))
    coverage=result.get('research_coverage') or {}
    if coverage.get('items'):
        story.extend([PageBreak(),Paragraph('研究覆盖与后续观察',styles['h1'])])
        labels={'checked':'已核查','conflict':'存在矛盾','missing':'缺少数据','not_applicable':'不适用','not_run':'尚未执行'}
        for item in coverage['items']:
            story.append(Paragraph(escape(str(item['label'])+' · '+labels.get(item['status'],item['status'])),styles['h2']))
            for key in ('claim','watch_next'):
                if item.get(key): story.append(Paragraph(escape(str(item[key])),styles['body']))
    links=source_links(result)
    if links:
        story.extend([PageBreak(),Paragraph('资料与原文索引',styles['h1'])])
        story.append(Paragraph('点击标题打开原文。网页中的本地计算证据需在原工作区查看；本 PDF 保留可公开访问的原文链接。',styles['body']))
        for i,item in enumerate(links,1):
            story.append(Paragraph(f'{i}. <a href="{escape(item["url"],quote=True)}" color="#087e8b">{escape(item["title"])}</a>',styles['body']))
    def footer(canvas,doc):
        canvas.setStrokeColor(colors.HexColor('#cbd8e0')); canvas.line(56,40,539,40)
        canvas.setFillColor(INK); canvas.setFont(font,8)
        canvas.drawString(56,26,'知衡 · 数据与证据驱动的投资研究')
        canvas.drawRightString(539,26,f'{doc.page}')
    doc.build(story,onFirstPage=footer,onLaterPages=footer)
    return out.getvalue()
