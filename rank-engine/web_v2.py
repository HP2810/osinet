import html
from fastapi import HTTPException
from fastapi.responses import HTMLResponse
import app as base
import enhancements as enh

app=base.app
app.router.routes=[r for r in app.router.routes if getattr(r,'path',None) not in ('/runs/{rid}','/runs/{rid}/report')]

EXTRA_CSS="""<style>
a.biz{font-weight:800;text-decoration:none}a.biz:hover{text-decoration:underline}details.why{margin-top:7px}details.why summary{cursor:pointer;color:#1d4ed8;font-weight:800;white-space:nowrap}.whybox{min-width:520px;max-width:760px;background:#f8fafc;border:1px solid #e2e8f0;border-radius:12px;padding:12px 16px;margin-top:8px}.whybox ul{padding-left:20px;margin:6px 0}.whybox li{margin:8px 0;line-height:1.45}.note{font-size:12px;color:#6b7280}.yes{color:#047857;font-weight:800}.no{color:#6b7280}@media(max-width:760px){.whybox{min-width:310px}}
</style>"""

def render(title,body,refresh=''):
    return base.page(title,EXTRA_CSS+body,refresh)

def website_cell(x):
    if not x.get('website'):return 'Not detected'
    u=html.escape(x['website'],quote=True); return f"<a target='_blank' rel='noopener' href='{u}'>Open site ↗</a>"

@app.get('/runs/{rid}',response_class=HTMLResponse)
def runpage_v2(rid:str):
    with base.db() as c:r=c.execute('select * from runs where id=?',(rid,)).fetchone()
    if not r:raise HTTPException(404)
    refresh='' if r['status'] in ('complete','failed') else '<meta http-equiv="refresh" content="7">'; arr=enh.aggregate(rid,r['valid'] or 0) if r['valid'] else []; leaders=sorted(arr,key=lambda x:(-x['top5'],x['median'],-x['visibility']))
    proxy='City-matched proxy configured' if base.proxy_cfg() else 'Server IP routing (city-matched proxy not configured)'
    body=f"<a href='/'>← New analysis</a><h1 style='font-size:46px'>{html.escape(r['query'])}</h1><span class='pill'>{r['status'].replace('_',' ')}</span><p>{html.escape(r['display_location'] or r['location'] or '')}</p><div class='metrics'><div class='metric'><b>{r['valid'] or 0}</b>valid searches</div><div class='metric'><b>{r['planned'] or base.GRID*base.GRID*base.REPS}</b>planned</div><div class='metric'><b>{r['failed'] or 0}</b>failed/empty</div><div class='metric'><b>{html.escape(r['timezone'] or '')}</b>timezone</div></div><div class='panel'><b>Network test mode:</b> {proxy}</div>"
    if r['status']=='failed':body+=f"<div class='panel bad'><b>Run failed:</b> {html.escape(r['error'] or '')}</div>"
    if leaders:
        rows=''
        for i,x in enumerate(leaders[:5],1):
            ad=f"<span class='yes'>Observed {x['ad_hits']}×</span>" if x['ad_hits'] else "<span class='no'>Not observed</span>"
            rows+=f"<tr><td>{i}</td><td>{enh.business_link(x)}{enh.explanation_html(x,arr,r['valid'] or 0)}</td><td>{x['top5']}%</td><td>#{x['median']}</td><td>{x['stability']}</td><td>{ad}</td><td>{website_cell(x)}</td></tr>"
        body+=f"<section class='panel'><h2>Top 5 most consistently visible businesses</h2><p>Click <b>Why is this business ranking?</b> for the plain-English evidence. Click the business name to open its exact Google Business Profile.</p><table><tr><th>#</th><th>Business + explanation</th><th>Top 5 coverage</th><th>Median rank</th><th>Stability</th><th>Google Ads</th><th>Website</th></tr>{rows}</table></section>"
        rows=''.join(f"<tr><td>{i}</td><td>{enh.business_link(x)}</td><td><b>{x['opportunity']}</b></td><td>{x['top5']}%</td><td>{website_cell(x)}</td><td>{x['site_score']}</td><td>{x['rating'] or ''}</td><td>{x['reviews'] or ''}</td><td>{'Observed' if x['ad_hits'] else 'Not observed'}</td></tr>" for i,x in enumerate(arr[:25],1))
        body+=f"<section class='panel'><h2>Highest sales opportunities</h2><table><tr><th>#</th><th>Business</th><th>Opportunity</th><th>Top 5</th><th>Website</th><th>Site score</th><th>Rating</th><th>Reviews</th><th>Ads</th></tr>{rows}</table></section>"
    if r['status']=='complete':body+=f"<div class='actions'><a class='btn' href='/runs/{rid}/report'>Open sales report</a><a class='btn' href='/runs/{rid}/csv'>Download CSV</a><a class='btn' href='/runs/{rid}/evidence'>View evidence</a></div>"
    return render('Analysis',body,refresh)

@app.get('/runs/{rid}/report',response_class=HTMLResponse)
def report_v2(rid:str):
    with base.db() as c:r=c.execute('select * from runs where id=?',(rid,)).fetchone()
    if not r:raise HTTPException(404)
    arr=enh.aggregate(rid,r['valid'] or 0); leaders=sorted(arr,key=lambda x:(-x['top5'],x['median'],-x['visibility'])); a=''
    for i,x in enumerate(leaders[:5],1):a+=f"<tr><td>{i}</td><td>{enh.business_link(x)}{enh.explanation_html(x,arr,r['valid'] or 0)}</td><td>{x['top5']}%</td><td>#{x['median']}</td><td>{'Observed' if x['ad_hits'] else 'Not observed'}</td></tr>"
    b=''.join(f"<tr><td>{i}</td><td>{enh.business_link(x)}</td><td><b>{x['opportunity']}</b></td><td>{website_cell(x)}</td><td>{x['site_score']}</td><td>{x['reviews'] or ''}</td></tr>" for i,x in enumerate(arr[:15],1))
    body=f"<a href='/runs/{rid}'>← Back</a><div class='eyebrow'>CLIENT-READY MARKET REPORT</div><h1 style='font-size:48px'>Who customers are finding first — and why</h1><p><b>Search tested:</b> {html.escape(r['query'])}</p><p><b>Area:</b> {html.escape(r['display_location'] or '')}</p><div class='metrics'><div class='metric'><b>{r['valid']}</b>valid clean searches</div><div class='metric'><b>{r['planned']}</b>planned tests</div><div class='metric'><b>{len(arr)}</b>businesses observed</div><div class='metric'><b>{'Yes' if base.proxy_cfg() else 'No'}</b>city-matched proxy</div></div><section class='panel'><h2>Top 5 most visible competitors</h2><p>Each explanation separates observed paid visibility from likely organic local-ranking contributors.</p><table><tr><th>#</th><th>Business + why</th><th>Top 5 coverage</th><th>Median rank</th><th>Google Ads</th></tr>{a}</table></section><section class='panel'><h2>Best website/SEO sales opportunities</h2><table><tr><th>#</th><th>Business</th><th>Opportunity</th><th>Website</th><th>Site score</th><th>Reviews</th></tr>{b}</table></section><p><small>Google documents relevance, distance and prominence as the main local ranking factors. Sponsored placements are detected separately. “Not observed” means no Sponsored/Ad label was captured in this scan; it does not prove the business never advertises.</small></p>"
    return render('Sales report',body)
