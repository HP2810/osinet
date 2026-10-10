from __future__ import annotations
import csv, html, io, json, math, os, re, sqlite3, threading, time, uuid
from collections import defaultdict
from contextlib import contextmanager
from pathlib import Path
from statistics import median, pstdev
from urllib.parse import quote, urlparse

import httpx
from bs4 import BeautifulSoup
from fastapi import FastAPI, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, Response, FileResponse
from playwright.sync_api import sync_playwright
from timezonefinder import TimezoneFinder

DATA=Path(os.getenv('PERSIST_ROOT','/data')); DATA.mkdir(parents=True,exist_ok=True)
EVID=DATA/'evidence'; EVID.mkdir(exist_ok=True)
DB=DATA/'rank.db'
GRID=int(os.getenv('GRID_SIZE','5')); REPS=int(os.getenv('REPETITIONS','3')); MAXR=int(os.getenv('MAX_RESULTS','12'))
app=FastAPI(title='Local Search Intelligence')

def db():
    c=sqlite3.connect(DB,check_same_thread=False); c.row_factory=sqlite3.Row; return c

def init():
    with db() as c:
        c.executescript('''
        create table if not exists runs(id text primary key, query text, service text, location text, display_location text,status text,created_at text default current_timestamp,finished_at text,error text,lat real,lon real,timezone text,planned int,valid int default 0,failed int default 0);
        create table if not exists obs(id integer primary key autoincrement,run_id text,point int,row_n int,col_n int,lat real,lon real,rep int,rank int,name text,maps_url text,raw text,screenshot text);
        create table if not exists scans(id integer primary key autoincrement,run_id text,point int,rep int,status text,count int,error text);
        create table if not exists details(run_id text,bkey text,name text,maps_url text,website text,address text,phone text,rating real,reviews int,site_score real,site_json text,primary key(run_id,bkey));
        ''')
init()

def parse(q):
    q=' '.join(q.strip().split()); ms=list(re.finditer(r'\s+in\s+',q,re.I))
    if not ms: return q,q
    m=ms[-1]; return q[:m.start()].strip(),q[m.end():].strip()

def geocode(place):
    h={'User-Agent':'LocalSearchIntelligence/1.0 contact research'}
    r=httpx.get('https://nominatim.openstreetmap.org/search',params={'q':place,'format':'jsonv2','limit':1,'addressdetails':1},headers=h,timeout=20); r.raise_for_status(); d=r.json()
    if not d: raise RuntimeError('Could not locate '+place)
    x=d[0]; return {'display':x.get('display_name',place),'lat':float(x['lat']),'lon':float(x['lon']),'bbox':[float(v) for v in x['boundingbox']]}

def lin(a,b,n): return [(a+b)/2] if n<=1 else [a+(b-a)*i/(n-1) for i in range(n)]
def grid(b):
    s,n,w,e=b; lp=(n-s)*.08; op=(e-w)*.08; lats=list(reversed(lin(s+lp,n-lp,GRID))); lons=lin(w+op,e-op,GRID)
    return [(r*GRID+c,r,c,la,lo) for r,la in enumerate(lats) for c,lo in enumerate(lons)]

def proxy_cfg():
    s=os.getenv('PROXY_SERVER','').strip()
    if not s:return None
    d={'server':s}; u=os.getenv('PROXY_USERNAME','').strip(); p=os.getenv('PROXY_PASSWORD','').strip()
    if u:d['username']=u
    if p:d['password']=p
    return d

@contextmanager
def browser(lat,lon,tz):
    p=sync_playwright().start(); kw={'headless':True}; px=proxy_cfg()
    if px:kw['proxy']=px
    b=p.chromium.launch(**kw); ctx=b.new_context(locale='en-US',timezone_id=tz,geolocation={'latitude':lat,'longitude':lon},permissions=['geolocation'],viewport={'width':1440,'height':1000},user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36'); page=ctx.new_page()
    try: yield page
    finally: ctx.close(); b.close(); p.stop()

def dismiss(page):
    for label in ['Reject all','Accept all','I agree']:
        try:
            x=page.get_by_role('button',name=label)
            if x.count() and x.first.is_visible(): x.first.click(timeout=1000); page.wait_for_timeout(500); return
        except: pass

def cards(page):
    out=[]; seen=set(); links=page.locator('div[role="feed"] a[href*="/maps/place/"]')
    for i in range(min(links.count(),MAXR*5)):
        try:
            a=links.nth(i); href=a.get_attribute('href') or ''; name=(a.get_attribute('aria-label') or a.inner_text(timeout=700) or '').strip(); name=re.sub(r'^Ad\s*[·•-]?\s*','',name,flags=re.I)
            if not name or href in seen:continue
            seen.add(href)
            try: raw=a.locator("xpath=ancestor::div[@role='article'][1]").inner_text(timeout=700)
            except: raw=name
            out.append((name[:200],href,raw[:3000]))
            if len(out)>=MAXR:break
        except:pass
    return out

def scan(q,rid,pt,rep,tz):
    idx,r,c,lat,lon=pt; folder=EVID/rid; folder.mkdir(parents=True,exist_ok=True); shot=folder/f'p{idx:03d}_r{rep}.png'
    with browser(lat,lon,tz) as page:
        page.goto('https://www.google.com/maps/search/'+quote(q)+'?hl=en',wait_until='domcontentloaded',timeout=45000); dismiss(page); page.wait_for_timeout(4000)
        try:
            feed=page.locator('div[role="feed"]')
            if feed.count():
                for _ in range(4): feed.evaluate('el=>el.scrollBy(0,el.scrollHeight)'); page.wait_for_timeout(700)
        except:pass
        page.screenshot(path=str(shot),full_page=True); rows=cards(page)
    return [(idx,r,c,lat,lon,rep,k+1,*x,str(shot)) for k,x in enumerate(rows)]

def key(n): return re.sub(r'[^a-z0-9]+',' ',n.lower()).strip()
def enrich(name,url,lat,lon,tz):
    d={'name':name,'maps_url':url,'website':'','address':'','phone':'','rating':None,'reviews':None,'site_score':0,'site_json':'{}'}
    if not url:return d
    try:
        with browser(lat,lon,tz) as p:
            p.goto(url,wait_until='domcontentloaded',timeout=45000); dismiss(p); p.wait_for_timeout(1800)
            try:
                x=p.locator('a[data-item-id="authority"]'); d['website']=x.first.get_attribute('href') or '' if x.count() else ''
            except:pass
            try:
                x=p.locator('button[data-item-id="address"]'); d['address']=(x.first.get_attribute('aria-label') or '').replace('Address: ','') if x.count() else ''
            except:pass
            try:
                x=p.locator('button[data-item-id^="phone:tel:"]'); d['phone']=(x.first.get_attribute('aria-label') or '').replace('Phone: ','') if x.count() else ''
            except:pass
            try:
                x=p.locator('div[role="main"] span[aria-hidden="true"]').filter(has_text=re.compile(r'^\d(?:\.\d)?$'))
                if x.count():d['rating']=float(x.first.inner_text().strip())
            except:pass
            try:
                x=p.locator('button[aria-label*="reviews"]')
                if x.count():
                    t=x.first.get_attribute('aria-label') or ''; m=re.search(r'([\d,]+)\s+reviews',t,re.I)
                    if m:d['reviews']=int(m.group(1).replace(',',''))
            except:pass
    except:pass
    if d['website']:
        try:
            rr=httpx.get(d['website'],timeout=12,follow_redirects=True,headers={'User-Agent':'Mozilla/5.0'}); rr.raise_for_status(); s=BeautifulSoup(rr.text,'html.parser'); final=str(rr.url)
            checks={'https':urlparse(final).scheme=='https','title':bool(s.title and s.title.get_text(strip=True)),'meta':bool(s.find('meta',attrs={'name':lambda x:x and x.lower()=='description'})),'h1':bool(s.find('h1')),'viewport':bool(s.find('meta',attrs={'name':lambda x:x and x.lower()=='viewport'})),'canonical':bool(s.find('link',rel='canonical')),'schema':'LocalBusiness' in rr.text}
            weights={'https':15,'title':15,'meta':15,'h1':15,'viewport':15,'canonical':10,'schema':15}; d['site_score']=sum(weights[k] for k,v in checks.items() if v); d['site_json']=json.dumps(checks)
        except Exception as e:d['site_json']=json.dumps({'error':str(e)[:200]})
    return d

def aggregate(rid,valid):
    with db() as c: obs=c.execute('select * from obs where run_id=?',(rid,)).fetchall(); ds={r['bkey']:r for r in c.execute('select * from details where run_id=?',(rid,)).fetchall()}
    g=defaultdict(list); urls={}
    for o in obs:g[key(o['name'])].append(o['rank']); urls[key(o['name'])]=(o['name'],o['maps_url'])
    arr=[]
    for k,rs in g.items():
        d=ds.get(k); name=(d['name'] if d else urls[k][0]); website=(d['website'] if d else ''); rating=d['rating'] if d else None; reviews=d['reviews'] if d else None; site=float(d['site_score'] or 0) if d else 0
        top5=100*sum(r<=5 for r in rs)/max(valid,1); top3=100*sum(r<=3 for r in rs)/max(valid,1); vis=100*sum(1/r for r in rs)/max(valid,1); stab=max(0,100-min(100,pstdev(rs)*12)) if len(rs)>1 else 100
        rat=max(0,min(1,((rating or 0)-3)/2)); rev=min(1,math.log1p(max(0,reviews or 0))/math.log1p(1000)); strength=100*(.45*rat+.55*rev)
        opp=strength*.30+(100-site)*.35+(100-min(100,top5))*.25+max(0,min(100,100-vis))*.10
        arr.append({'key':k,'name':name,'website':website,'address':d['address'] if d else '','phone':d['phone'] if d else '','rating':rating,'reviews':reviews,'site_score':round(site,1),'top5':round(top5,1),'top3':round(top3,1),'median':median(rs),'avg':round(sum(rs)/len(rs),2),'coverage':round(100*len(rs)/max(valid,1),1),'visibility':round(vis,1),'stability':round(stab,1),'opportunity':round(opp,1),'maps_url':d['maps_url'] if d else urls[k][1]})
    return sorted(arr,key=lambda x:(-x['opportunity'],x['median']))

def worker(rid,q,loc):
    try:
        with db() as c:c.execute("update runs set status='geocoding' where id=?",(rid,))
        geo=geocode(loc); tz=TimezoneFinder().timezone_at(lat=geo['lat'],lng=geo['lon']) or 'America/New_York'; pts=grid(geo['bbox']); planned=len(pts)*REPS
        with db() as c:c.execute("update runs set status='scanning',display_location=?,lat=?,lon=?,timezone=?,planned=? where id=?",(geo['display'],geo['lat'],geo['lon'],tz,planned,rid))
        valid=failed=0
        for pt in pts:
            for rep in range(1,REPS+1):
                try:
                    rows=scan(q,rid,pt,rep,tz)
                    with db() as c:
                        if rows:
                            c.executemany('insert into obs(run_id,point,row_n,col_n,lat,lon,rep,rank,name,maps_url,raw,screenshot) values(?,?,?,?,?,?,?,?,?,?,?,?)',[(rid,*x) for x in rows]); c.execute('insert into scans(run_id,point,rep,status,count,error) values(?,?,?,?,?,?)',(rid,pt[0],rep,'ok',len(rows),'')); valid+=1
                        else:c.execute('insert into scans(run_id,point,rep,status,count,error) values(?,?,?,?,?,?)',(rid,pt[0],rep,'empty',0,'No cards detected')); failed+=1
                        c.execute('update runs set valid=?,failed=? where id=?',(valid,failed,rid))
                except Exception as e:
                    with db() as c:c.execute('insert into scans(run_id,point,rep,status,count,error) values(?,?,?,?,?,?)',(rid,pt[0],rep,'error',0,str(e)[:500])); c.execute('update runs set failed=? where id=?',(failed+1,rid)); failed+=1
                time.sleep(1.2)
        if not valid:raise RuntimeError('No valid Google Maps result pages were captured. Google may have challenged the hosted browser or the target page changed.')
        with db() as c:c.execute("update runs set status='enriching' where id=?",(rid,)); seeds=c.execute('select name,maps_url,count(*) n from obs where run_id=? group by name,maps_url order by n desc limit 40',(rid,)).fetchall()
        for s in seeds:
            d=enrich(s['name'],s['maps_url'],geo['lat'],geo['lon'],tz)
            with db() as c:c.execute('insert or replace into details(run_id,bkey,name,maps_url,website,address,phone,rating,reviews,site_score,site_json) values(?,?,?,?,?,?,?,?,?,?,?)',(rid,key(d['name']),d['name'],d['maps_url'],d['website'],d['address'],d['phone'],d['rating'],d['reviews'],d['site_score'],d['site_json']))
        with db() as c:c.execute("update runs set status='complete',finished_at=current_timestamp where id=?",(rid,))
    except Exception as e:
        with db() as c:c.execute("update runs set status='failed',finished_at=current_timestamp,error=? where id=?",(str(e)[:2000],rid))

def page(title,body,refresh=''):
    return f'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(title)}</title>{refresh}<style>:root{{font-family:Inter,system-ui,sans-serif;color:#131313;background:#f6f7f9}}*{{box-sizing:border-box}}body{{margin:0}}main{{max-width:1180px;margin:auto;padding:46px 22px}}h1{{font-size:clamp(36px,6vw,70px);line-height:1.02;letter-spacing:-.045em;margin:14px 0}}p{{color:#59606b;line-height:1.55}}.hero{{padding:55px 0}}.eyebrow{{font-size:12px;font-weight:800;letter-spacing:.16em;color:#6b7280}}form{{display:flex;gap:10px;margin:30px 0}}input{{flex:1;padding:18px 20px;border:1px solid #ccd1d8;border-radius:13px;font-size:18px}}button,.btn{{padding:17px 22px;border:0;border-radius:13px;background:#111827;color:#fff;font-weight:800;text-decoration:none;display:inline-block}}.panel,.metric{{background:#fff;border:1px solid #e1e5ea;border-radius:16px;padding:20px;margin:18px 0;overflow:auto}}.metrics{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}}.metric b{{font-size:27px;display:block}}table{{width:100%;border-collapse:collapse;min-width:780px}}th,td{{padding:11px;border-bottom:1px solid #edf0f3;text-align:left}}th{{font-size:12px;color:#6b7280;text-transform:uppercase}}.bad{{background:#fee2e2}}.pill{{display:inline-block;padding:7px 10px;border-radius:999px;background:#e9edf2;font-weight:700}}.actions{{display:flex;gap:10px;flex-wrap:wrap;margin:20px 0}}a{{color:#1d4ed8}}@media(max-width:760px){{form{{flex-direction:column}}.metrics{{grid-template-columns:1fr 1fr}}}}</style></head><body><main>{body}</main></body></html>'''

@app.get('/health')
def health():return {'ok':True}
@app.get('/',response_class=HTMLResponse)
def home():
    with db() as c:runs=c.execute('select * from runs order by created_at desc limit 12').fetchall()
    rows=''.join(f"<tr><td>{html.escape(r['query'])}</td><td>{r['status']}</td><td>{r['created_at']}</td><td><a href='/runs/{r['id']}'>Open</a></td></tr>" for r in runs)
    b='''<section class="hero"><div class="eyebrow">NATIONWIDE LOCAL SEARCH INTELLIGENCE</div><h1>One search. Real competitive evidence.</h1><p>Enter exactly what a customer would search anywhere in the United States.</p><form method="post" action="/run"><input name="query" required placeholder="best nail salon in Flushing, Queens, New York"><button>Analyze market</button></form><p>Fresh browser sessions, geo-grid testing, repeated rankings, screenshots, website audits and sales-opportunity scoring.</p></section>'''
    if rows:b+=f'<section class="panel"><h2>Recent analyses</h2><table><tr><th>Query</th><th>Status</th><th>Started</th><th></th></tr>{rows}</table></section>'
    return page('Local Search Intelligence',b)
@app.post('/run')
def run(query:str=Form(...)):
    service,loc=parse(query); rid=uuid.uuid4().hex[:12]
    with db() as c:c.execute('insert into runs(id,query,service,location,status) values(?,?,?,?,?)',(rid,query,service,loc,'queued'))
    threading.Thread(target=worker,args=(rid,query,loc),daemon=True).start(); return RedirectResponse('/runs/'+rid,303)
@app.get('/runs/{rid}',response_class=HTMLResponse)
def runpage(rid:str):
    with db() as c:r=c.execute('select * from runs where id=?',(rid,)).fetchone()
    if not r:raise HTTPException(404)
    refresh='' if r['status'] in ('complete','failed') else '<meta http-equiv="refresh" content="7">'; arr=aggregate(rid,r['valid'] or 0) if r['valid'] else []; leaders=sorted(arr,key=lambda x:(-x['top5'],x['median'],-x['visibility']))
    proxy='City-matched proxy configured' if proxy_cfg() else 'Server IP routing (city-matched proxy not configured)'
    body=f"<a href='/'>← New analysis</a><h1 style='font-size:46px'>{html.escape(r['query'])}</h1><span class='pill'>{r['status'].replace('_',' ')}</span><p>{html.escape(r['display_location'] or r['location'] or '')}</p><div class='metrics'><div class='metric'><b>{r['valid'] or 0}</b>valid searches</div><div class='metric'><b>{r['planned'] or GRID*GRID*REPS}</b>planned</div><div class='metric'><b>{r['failed'] or 0}</b>failed/empty</div><div class='metric'><b>{html.escape(r['timezone'] or '')}</b>timezone</div></div><div class='panel'><b>Network test mode:</b> {proxy}</div>"
    if r['status']=='failed':body+=f"<div class='panel bad'><b>Run failed:</b> {html.escape(r['error'] or '')}</div>"
    if leaders:
        rows=''.join(f"<tr><td>{i}</td><td>{html.escape(x['name'])}</td><td>{x['top5']}%</td><td>#{x['median']}</td><td>{x['stability']}</td></tr>" for i,x in enumerate(leaders[:15],1)); body+=f"<section class='panel'><h2>Businesses most consistently visible</h2><table><tr><th>#</th><th>Business</th><th>Top 5 coverage</th><th>Median rank</th><th>Stability</th></tr>{rows}</table></section>"
        rows=''.join(f"<tr><td>{i}</td><td>{html.escape(x['name'])}</td><td><b>{x['opportunity']}</b></td><td>{x['top5']}%</td><td>{'None' if not x['website'] else 'Yes'}</td><td>{x['site_score']}</td><td>{x['rating'] or ''}</td><td>{x['reviews'] or ''}</td></tr>" for i,x in enumerate(arr[:25],1)); body+=f"<section class='panel'><h2>Highest sales opportunities</h2><table><tr><th>#</th><th>Business</th><th>Opportunity</th><th>Top 5</th><th>Website</th><th>Site score</th><th>Rating</th><th>Reviews</th></tr>{rows}</table></section>"
    if r['status']=='complete':body+=f"<div class='actions'><a class='btn' href='/runs/{rid}/report'>Open sales report</a><a class='btn' href='/runs/{rid}/csv'>Download CSV</a><a class='btn' href='/runs/{rid}/evidence'>View evidence</a></div>"
    return page('Analysis',body,refresh)
@app.get('/runs/{rid}/report',response_class=HTMLResponse)
def report(rid:str):
    with db() as c:r=c.execute('select * from runs where id=?',(rid,)).fetchone()
    if not r:raise HTTPException(404)
    arr=aggregate(rid,r['valid'] or 0); leaders=sorted(arr,key=lambda x:(-x['top5'],x['median']))
    a=''.join(f"<tr><td>{i}</td><td>{html.escape(x['name'])}</td><td>{x['top5']}%</td><td>#{x['median']}</td></tr>" for i,x in enumerate(leaders[:10],1)); b=''.join(f"<tr><td>{i}</td><td>{html.escape(x['name'])}</td><td><b>{x['opportunity']}</b></td><td>{'No website' if not x['website'] else 'Website found'}</td><td>{x['site_score']}</td><td>{x['reviews'] or ''}</td></tr>" for i,x in enumerate(arr[:15],1))
    body=f"<a href='/runs/{rid}'>← Back</a><div class='eyebrow'>CLIENT-READY MARKET REPORT</div><h1 style='font-size:48px'>Who customers are finding first</h1><p><b>Search tested:</b> {html.escape(r['query'])}</p><p><b>Area:</b> {html.escape(r['display_location'] or '')}</p><div class='metrics'><div class='metric'><b>{r['valid']}</b>valid clean searches</div><div class='metric'><b>{r['planned']}</b>planned tests</div><div class='metric'><b>{len(arr)}</b>businesses observed</div><div class='metric'><b>{'Yes' if proxy_cfg() else 'No'}</b>city-matched proxy</div></div><section class='panel'><h2>Most visible competitors</h2><table><tr><th>#</th><th>Business</th><th>Top 5 coverage</th><th>Median rank</th></tr>{a}</table></section><section class='panel'><h2>Best website/SEO sales opportunities</h2><table><tr><th>#</th><th>Business</th><th>Opportunity</th><th>Website</th><th>Site score</th><th>Reviews</th></tr>{b}</table></section><p><small>This is a controlled local-search benchmark. Google rankings can vary by location, time, device and other context. Failed/challenged searches are excluded rather than counted as poor rankings.</small></p>"
    return page('Sales report',body)
@app.get('/runs/{rid}/csv')
def csvout(rid:str):
    with db() as c:r=c.execute('select * from runs where id=?',(rid,)).fetchone()
    if not r:raise HTTPException(404)
    arr=aggregate(rid,r['valid'] or 0); s=io.StringIO(); fields=['name','opportunity','top5','top3','median','avg','coverage','visibility','stability','rating','reviews','website','site_score','address','phone','maps_url']; w=csv.DictWriter(s,fieldnames=fields,extrasaction='ignore'); w.writeheader(); w.writerows(arr); return Response(s.getvalue(),media_type='text/csv',headers={'Content-Disposition':f'attachment; filename=report_{rid}.csv'})
@app.get('/runs/{rid}/evidence',response_class=HTMLResponse)
def evid(rid:str):
    folder=EVID/rid; files=sorted(folder.glob('*.png')) if folder.exists() else []; links=''.join(f"<li><a target='_blank' href='/evidence/{rid}/{f.name}'>{f.name}</a></li>" for f in files); return page('Evidence',f"<a href='/runs/{rid}'>← Back</a><h1 style='font-size:44px'>Evidence</h1><p>{len(files)} captured Google screenshots.</p><div class='panel'><ul>{links}</ul></div>")
@app.get('/evidence/{rid}/{name}')
def evfile(rid:str,name:str):
    p=EVID/rid/Path(name).name
    if not p.exists():raise HTTPException(404)
    return FileResponse(p,media_type='image/png')
