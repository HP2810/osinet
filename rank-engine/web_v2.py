import html
from fastapi import HTTPException
from fastapi.responses import HTMLResponse
import app as base
import location_fixes  # patches geocoding + scan diagnostics before enhancements wrap the worker
import enhancements as enh
import profile_signals
profile_signals.patch()
import ranking_insights
ranking_insights.patch()
import google_ranking_framework
google_ranking_framework.patch()

app=base.app
app.router.routes=[r for r in app.router.routes if getattr(r,'path',None) not in ('/runs/{rid}','/runs/{rid}/report')]

EXTRA_CSS="""<style>
a.biz{font-weight:800;text-decoration:none}a.biz:hover{text-decoration:underline}details.why{margin-top:7px}details.why summary{cursor:pointer;color:#1d4ed8;font-weight:800;white-space:nowrap}.whybox{min-width:560px;max-width:820px;background:#f8fafc;border:1px solid #e2e8f0;border-radius:12px;padding:12px 16px;margin-top:8px}.whybox ul{padding-left:20px;margin:6px 0}.whybox li{margin:8px 0;line-height:1.45}.note{font-size:12px;color:#6b7280}.yes{color:#047857;font-weight:800}.no{color:#6b7280}.bottomline{margin:12px 0;padding:12px 14px;background:#ecfdf5;border:1px solid #a7f3d0;border-radius:10px;line-height:1.5}.notwhy{margin:12px 0;padding:12px 14px;background:#fff7ed;border:1px solid #fed7aa;border-radius:10px;line-height:1.5}.framework{background:#eff6ff;border:1px solid #bfdbfe}.framework b{color:#1e3a8a}.framework a{font-weight:800}@media(max-width:760px){.whybox{min-width:310px}}
</style>"""

def render(title,body,refresh=''):
    return base.page(title,EXTRA_CSS+body,refresh)

def website_cell(x):
    if not x.get('website'):return 'Not verified'
    u=html.escape(x['website'],quote=True); return f"<a target='_blank' rel='noopener' href='{u}'>Open site ↗</a>"

def framework_panel():
    return """<div class='panel framework'><b>Google-documented local-ranking model:</b> Relevance · Distance · Prominence. Sponsored/Ad placements are measured separately and excluded from organic rank calculations. The detailed Google algorithm and factor weights are not public, so this system reports exact observed evidence plus clearly labeled interpretation—not invented causal certainty. <a target='_blank' rel='noopener' href='https://support.google.com/business/answer/7091'>Google documentation ↗</a></div>"""

@app.get('/runs/{rid}',response_class=HTMLResponse)
def runpage_v2(rid:str):
    with base.db() as c:r=c.execute('select * from runs where id=?',(rid,)).fetchone()
    if not r:raise HTTPException(404)
    refresh='' if r['status'] in ('complete','failed') else '<meta http-equiv="refresh" content="7">'; arr=enh.aggregate(rid,r['valid'] or 0) if r['valid'] else []; leaders=sorted(arr,key=lambda x:(-x['top5'],-x.get('top3',0),-x.get('coverage',0),x['median'],-x['visibility']))
    proxy='City-matched proxy configured' if base.proxy_cfg() else 'Server IP routing (city-matched proxy not configured)'
    completeness=round(100*(r['valid'] or 0)/(r['planned'] or 1),1) if r['planned'] else 0
    body=f"<a href='/'>← New analysis</a><h1 style='font-size:46px'>{html.escape(r['query'])}</h1><span class='pill'>{r['status'].replace('_',' ')}</span><p>{html.escape(r['display_location'] or r['location'] or '')}</p><div class='metrics'><div class='metric'><b>{r['valid'] or 0}</b>valid searches</div><div class='metric'><b>{r['planned'] or base.GRID*base.GRID*base.REPS}</b>planned</div><div class='metric'><b>{completeness}%</b>evidence completeness</div><div class='metric'><b>{html.escape(r['timezone'] or '')}</b>timezone</div></div><div class='panel'><b>Network test mode:</b> {proxy}</div>{framework_panel()}"
    if r['status']=='failed':body+=f"<div class='panel bad'><b>Run failed:</b> {html.escape(r['error'] or '')}</div>"
    if leaders:
        rows=''
        for i,x in enumerate(leaders[:5],1):
            ad=f"<span class='yes'>Observed {x['ad_hits']}×</span>" if x['ad_hits'] else "<span class='no'>Not observed</span>"
            fw=x.get('google_framework') or {}; fc=fw.get('factor_coverage')
            coverage=f"{fc:.0f}%" if fc is not None else '—'
            rows+=f"<tr><td>{i}</td><td>{enh.business_link(x)}{enh.explanation_html(x,arr,r['valid'] or 0)}</td><td>{x['top5']}%</td><td>#{x['median']}</td><td>{coverage}</td><td>{ad}</td><td>{website_cell(x)}</td></tr>"
        body+=f"<section class='panel'><h2>Top 5 most consistently visible businesses</h2><p>Click <b>Why is this business actually on top?</b> to see the Google-documented factor matrix, exact scan evidence, missing-data boundaries, and business-specific conclusion.</p><table><tr><th>#</th><th>Business + evidence</th><th>Top 5 coverage</th><th>Median organic rank</th><th>Factor data coverage</th><th>Google Ads</th><th>Website</th></tr>{rows}</table></section>"
        rows=''.join(f"<tr><td>{i}</td><td>{enh.business_link(x)}</td><td><b>{x['opportunity']}</b></td><td>{x['top5']}%</td><td>{website_cell(x)}</td><td>{x['site_score']}</td><td>{x['rating'] or ''}</td><td>{x['reviews'] or ''}</td><td>{'Observed' if x['ad_hits'] else 'Not observed'}</td></tr>" for i,x in enumerate(arr[:25],1))
        body+=f"<section class='panel'><h2>Highest sales opportunities</h2><p>The website score below is our audit score, not a Google ranking score.</p><table><tr><th>#</th><th>Business</th><th>Opportunity</th><th>Top 5</th><th>Website</th><th>Our site audit</th><th>Rating</th><th>Reviews</th><th>Ads</th></tr>{rows}</table></section>"
    if r['status']=='complete':body+=f"<div class='actions'><a class='btn' href='/runs/{rid}/report'>Open sales report</a><a class='btn' href='/runs/{rid}/csv'>Download CSV</a><a class='btn' href='/runs/{rid}/evidence'>View evidence</a></div>"
    return render('Analysis',body,refresh)

@app.get('/runs/{rid}/report',response_class=HTMLResponse)
def report_v2(rid:str):
    with base.db() as c:r=c.execute('select * from runs where id=?',(rid,)).fetchone()
    if not r:raise HTTPException(404)
    arr=enh.aggregate(rid,r['valid'] or 0); leaders=sorted(arr,key=lambda x:(-x['top5'],-x.get('top3',0),-x.get('coverage',0),x['median'],-x['visibility'])); a=''
    for i,x in enumerate(leaders[:5],1):
        fw=x.get('google_framework') or {}; fc=fw.get('factor_coverage'); c=f"{fc:.0f}%" if fc is not None else '—'
        a+=f"<tr><td>{i}</td><td>{enh.business_link(x)}{enh.explanation_html(x,arr,r['valid'] or 0)}</td><td>{x['top5']}%</td><td>#{x['median']}</td><td>{c}</td><td>{'Observed' if x['ad_hits'] else 'Not observed'}</td></tr>"
    b=''.join(f"<tr><td>{i}</td><td>{enh.business_link(x)}</td><td><b>{x['opportunity']}</b></td><td>{website_cell(x)}</td><td>{x['site_score']}</td><td>{x['reviews'] or ''}</td></tr>" for i,x in enumerate(arr[:15],1))
    completeness=round(100*(r['valid'] or 0)/(r['planned'] or 1),1) if r['planned'] else 0
    body=f"<a href='/runs/{rid}'>← Back</a><div class='eyebrow'>CLIENT-READY MARKET REPORT</div><h1 style='font-size:48px'>Who customers are finding first — and what the evidence supports</h1><p><b>Search tested:</b> {html.escape(r['query'])}</p><p><b>Area:</b> {html.escape(r['display_location'] or '')}</p><div class='metrics'><div class='metric'><b>{r['valid']}</b>valid searches</div><div class='metric'><b>{r['planned']}</b>planned tests</div><div class='metric'><b>{completeness}%</b>evidence completeness</div><div class='metric'><b>{len(arr)}</b>businesses observed</div></div>{framework_panel()}<section class='panel'><h2>Top 5 most visible competitors</h2><p>Each explanation is organized around Google's documented relevance, distance and prominence framework. Every unavailable signal is shown as unavailable rather than guessed.</p><table><tr><th>#</th><th>Business + factor evidence</th><th>Top 5 coverage</th><th>Median rank</th><th>Factor data coverage</th><th>Google Ads</th></tr>{a}</table></section><section class='panel'><h2>Best website/SEO sales opportunities</h2><p>Our website audit is a sales diagnostic, not a Google ranking score.</p><table><tr><th>#</th><th>Business</th><th>Opportunity</th><th>Website</th><th>Our site audit</th><th>Reviews</th></tr>{b}</table></section><p><small>Accuracy statement: numeric ranking, review, rating, distance-pattern and ad-observation claims come from captured evidence in this audit. Google does not publish the business-by-business factor weights or causal explanation used for a specific local result, so no external system can truthfully guarantee 100% causal attribution.</small></p>"
    return render('Sales report',body)
