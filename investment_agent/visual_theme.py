"""A restrained, dark research-terminal visual system; no remote assets."""
from html import escape
import streamlit as st

ICONS={
 'radar':'<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><path d="M12 12 19 5M12 3v2M3 12h2M12 19v2M19 12h2"/>',
 'chart':'<path d="M4 4v16h17M7 15l4-5 4 3 5-7"/>',
 'layers':'<path d="m3 7 9-4 9 4-9 4-9-4Zm0 5 9 4 9-4M3 17l9 4 9-4"/>',
 'check':'<path d="m5 12 4 4 10-10"/><circle cx="12" cy="12" r="10"/>',
 'focus':'<path d="M8 3H3v5M16 3h5v5M21 16v5h-5M8 21H3v-5"/><circle cx="12" cy="12" r="4"/>',
 'pulse':'<path d="M2 12h5l3-8 4 16 3-8h5"/>',
}

def icon(name):
    return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'+ICONS.get(name,ICONS['chart'])+'</svg>'

def apply_theme():
    st.markdown('''<style>
    :root {--zh-bg:#0b1220;--zh-panel:#111c2c;--zh-line:#263347;--zh-text:#dfebf7;--zh-muted:#8fa4bc;--zh-accent:#59d6d0;}
    .stApp {background:var(--zh-bg);color:var(--zh-text);font-family:'Segoe UI','Microsoft YaHei',sans-serif;}
    [data-testid="stHeader"] {background:#0b1220e8;}
    [data-testid="stAppDeployButton"] {display:none;}
    [data-testid="stSidebar"] {background:#0d1726;border-right:1px solid #263347;}
    h1,h2,h3,h4 {color:#eaf4fc!important;letter-spacing:-.025em;}
    h1 {font-size:2.4rem!important;font-weight:650!important;}
    [data-testid="stCaptionContainer"] {color:#98adc2;}
    .stMainBlockContainer,.block-container {max-width:1280px;margin-inline:auto;padding:2rem clamp(1rem,3vw,3rem) 5rem;}
    [data-testid="stBottomBlockContainer"] {max-width:1280px;margin-inline:auto;background:#0b1220;padding-inline:clamp(1rem,3vw,3rem);}
    [data-testid="stMarkdownContainer"] {min-width:0;overflow-wrap:anywhere;line-height:1.8;}
    [data-testid="stMarkdownContainer"] p {line-height:1.85;}
    [data-testid="stMarkdownContainer"] a {color:#69d9d5;text-underline-offset:4px;}
    [data-testid="stMarkdownContainer"] h2 {font-size:1.5rem;margin-top:1.5rem;}
    [data-testid="stMarkdownContainer"] h3 {font-size:1.1rem;margin-top:1.25rem;}
    [data-testid="stVerticalBlockBorderWrapper"]>div {border-color:#263347!important;border-radius:14px;}
    [data-testid="stChatMessage"] {background:#111c2c;border:1px solid #263347;border-radius:16px;padding:1.4rem;gap:.75rem;box-sizing:border-box;}
    [data-testid="stChatMessageContent"] {flex:1 1 0%;min-width:0;width:100%;max-width:100%;}
    [data-testid="stChatMessageAvatarAssistant"] {display:none;}
    [data-testid="stTable"] table {width:100%;table-layout:auto;}
    [data-testid="stTable"], [data-testid="stMarkdownContainer"] table {max-width:100%;overflow-x:auto;}
    [data-testid="stTable"] th,[data-testid="stTable"] td {white-space:normal;overflow-wrap:anywhere;padding:.65rem .75rem;border-color:#263347;}
    [data-testid="stTable"] th {background:#17263a;color:#bdccdf;}
    .stButton>button,.stDownloadButton>button,[data-testid="stLinkButton"] a {border-radius:9px;border-color:#30425b;background:#142238;color:#dbe9f7;transition:border-color .2s,background .2s;}
    .stButton>button:hover,[data-testid="stLinkButton"] a:hover {border-color:#59d6d0;color:#77e8df;}
    .st-key-research-columns button {height:auto!important;min-height:2.6rem;}
    .st-key-research-columns button p {white-space:normal!important;overflow:visible!important;text-overflow:clip!important;-webkit-line-clamp:unset!important;}
    @media(max-width:1050px) {.st-key-research-evidence {display:none;}.st-key-research-columns [data-testid="stHorizontalBlock"] {flex-wrap:wrap!important;}.st-key-research-columns [data-testid="stColumn"] {width:100%!important;flex:1 1 100%!important;min-width:0!important;}}
    .stButton>button[kind="primary"] {background:#59d6d0;border-color:#59d6d0;color:#09252c;font-weight:650;}
    [data-baseweb="select"]>div,[data-baseweb="input"],[data-baseweb="textarea"] {background:#111f32;color:#dfeaf6;border-color:#30425b;}
    [data-testid="stExpander"] {border-color:#263347;border-radius:12px;background:#101b2b;}
    [data-testid="stMetricValue"] {font-family:'Bahnschrift','Consolas',monospace;}
    .zh-brand {display:flex;align-items:center;gap:10px;font-size:1.3rem;font-weight:700;color:#edf6ff;margin:0 0 10px;}
    .zh-brand svg {width:29px;height:29px;color:#59d6d0;}
    .zh-eyebrow {color:#59d6d0;font:11px 'Consolas',monospace;letter-spacing:.22em;text-transform:uppercase;margin-bottom:14px;}
    .zh-hero {position:relative;overflow:hidden;padding:28px 32px;border:1px solid #2b4257;border-radius:17px;background:#111c2c;margin:0 0 20px;}
    .zh-hero:after {display:none;content:'';position:absolute;right:-40px;top:-90px;width:340px;height:340px;border:1px solid #59d6d022;border-radius:50%;box-shadow:0 0 0 35px #59d6d009,0 0 0 70px #59d6d007;pointer-events:none;}
    .zh-hero h1 {font-size:2.15rem!important;line-height:1.3;margin:0 0 12px;padding:0;}
    .zh-hero p {color:#9fb5ca;font-size:.9rem;margin:0;max-width:850px;position:relative;z-index:1;}
    .zh-badge {display:inline-flex;gap:7px;align-items:center;padding:4px 10px;border-radius:6px;border:1px solid #3c5964;color:#8cddd6;font-size:11px;margin-top:18px;background:#142b37;}
    .zh-grid {display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px;margin:6px 0 26px;}
    .zh-kpi {background:#111c2c;border:1px solid #263347;border-radius:12px;padding:18px 20px;min-width:0;}
    .zh-kpi-head {display:flex;justify-content:space-between;align-items:center;color:#a4b8ce;font-size:12px;}
    .zh-kpi svg {height:19px;width:19px;color:#62c8cd;}
    .zh-kpi-value {font:32px 'Bahnschrift','Consolas',monospace;color:#f0f7ff;line-height:1.3;margin:10px 0 5px;}
    .zh-kpi-note {color:#849bb3;font-size:11px;}
    .zh-section {display:flex;align-items:center;gap:9px;color:#e2edf9;font-size:16px;font-weight:600;padding:2px 0 14px;}
    .zh-section svg {width:20px;height:20px;color:#59d6d0;}
    .zh-section small {margin-left:auto;color:#7591ab;font:10px 'Consolas',monospace;letter-spacing:.08em;}
    .zh-quote {display:flex;align-items:baseline;gap:16px;flex-wrap:wrap;margin:4px 0 9px;}
    .zh-price {font:32px 'Bahnschrift','Consolas',monospace;color:#edf5ff;}
    .zh-up {color:#f47582;font:14px 'Consolas',monospace;}.zh-down {color:#45c5a1;font:14px 'Consolas',monospace;}
    .zh-meta {font-size:11px;color:#91a7bd;}
    .zh-empty {border:1px dashed #36516a;border-radius:16px;padding:45px;color:#a4bbd1;text-align:center;}
    [data-testid="stPlotlyChart"] {border-radius:12px;overflow:hidden;}
    @media(max-width:760px) {.stMainBlockContainer,.block-container {padding:4.5rem .8rem 4rem;}.zh-hero {padding:22px 20px;}.zh-hero h1 {font-size:1.7rem!important;}.zh-grid {grid-template-columns:repeat(2,minmax(0,1fr));gap:9px;}.zh-kpi {padding:14px;}.zh-kpi-value {font-size:27px;}[data-testid="stChatMessage"] {padding:.75rem;}.zh-section small {display:none;}}
    @media(prefers-reduced-motion:reduce) {* {transition:none!important;}}
    .zh-launch {position:relative;isolation:isolate;min-height:480px;border:1px solid #304154;border-radius:22px;background-color:#07111e;background-size:cover;background-position:center;overflow:hidden;margin-bottom:20px;}
    .zh-launch-copy {position:relative;padding:46px 40px;max-width:610px;z-index:1;}
    .zh-launch h1 {font-size:clamp(2.8rem,4.2vw,4.4rem)!important;line-height:1.24;letter-spacing:-.055em!important;font-weight:750!important;margin:22px 0!important;padding:0;}
    .zh-launch h1 span {color:#a9e7e6;}
    .zh-launch p {color:#c0cfdf;font-size:17px;line-height:1.9;margin-bottom:24px;}
    .zh-launch-tag {display:inline-block;border-top:1px solid #62819770;padding-top:16px;color:#b5c9dc;letter-spacing:.12em;font-size:12px;}
    .zh-launch-foot {position:absolute;bottom:20px;right:26px;color:#c3d3df;font-size:11px;letter-spacing:.08em;}
    .zh-product-heading {display:flex;align-items:baseline;justify-content:space-between;gap:16px;margin:38px 0 19px;font-size:20px;font-weight:650;}
    .zh-product-heading small {font:10px Consolas,monospace;color:#819cb4;letter-spacing:.12em;}
    .zh-product-grid {display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px;margin-bottom:30px;}
    .zh-product-card {border:1px solid #273a4e;border-radius:14px;background:linear-gradient(150deg,#142333,#101925);padding:25px 23px;}
    .zh-product-card svg {width:28px;height:28px;display:block;color:#89d7db;margin-bottom:24px;}
    .zh-product-card small {font:10px Consolas,monospace;letter-spacing:.14em;color:#91aabe;}
    .zh-product-card h3 {margin:10px 0!important;font-size:17px!important;font-weight:650;line-height:1.5;}
    .zh-product-card p {color:#9db0c4;font-size:13px;margin:0;line-height:1.9;}
    [data-testid="stSidebar"] [data-testid="stRadio"] label {padding:7px 2px;}
    [data-testid="stTabs"] [role="tablist"] {gap:26px;border-bottom:1px solid #304256;margin-bottom:18px;}
    [data-testid="stTabs"] [role="tab"] {font-size:14px;padding-block:10px;}
    .st-key-research-columns [data-testid="stChatInput"] {border:1px solid #67889b;border-radius:14px;box-shadow:0 0 0 4px #59d6d007;}
    @media(max-width:1100px) {.zh-launch-copy {padding:38px 30px;}.zh-launch {min-height:440px;}.zh-product-grid {gap:10px;}.zh-product-card {padding:20px 15px;}.zh-product-heading small {display:none;}}
    @media(max-width:760px) {.zh-launch {min-height:410px;background-position:62% center;}.zh-launch-copy {padding:30px 23px;}.zh-launch h1 {font-size:2.7rem!important;}.zh-launch p {font-size:15px;}.zh-launch .zh-eyebrow {font-size:9px;letter-spacing:.12em;}.zh-launch-foot {left:23px;right:16px;font-size:10px;}.zh-product-grid {grid-template-columns:1fr;}.zh-product-card svg {margin-bottom:12px;}.zh-product-heading {font-size:18px;}}
    </style>''',unsafe_allow_html=True)

def hero(title,subtitle,badge='A-SHARE / RESEARCH WORKSPACE'):
    st.markdown(f'<div class="zh-hero"><div class="zh-eyebrow">ZHIHENG / INTELLIGENCE DESK</div><h1>{escape(title)}</h1><p>{escape(subtitle)}</p><div class="zh-badge">◈ &nbsp; {escape(badge)}</div></div>',unsafe_allow_html=True)

def section(title,tag='',symbol='chart'):
    st.markdown(f'<div class="zh-section">{icon(symbol)}<span>{escape(title)}</span><small>{escape(tag)}</small></div>',unsafe_allow_html=True)

def kpis(items):
    cards=[]
    for label,value,note,symbol in items:
        cards.append(f'<div class="zh-kpi"><div class="zh-kpi-head">{escape(label)}{icon(symbol)}</div><div class="zh-kpi-value">{escape(str(value))}</div><div class="zh-kpi-note">{escape(note)}</div></div>')
    st.markdown('<div class="zh-grid">'+''.join(cards)+'</div>',unsafe_allow_html=True)
