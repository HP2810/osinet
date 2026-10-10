from __future__ import annotations
import html, json, math, re
from collections import defaultdict
from statistics import median, pstdev
from urllib.parse import parse_qs, unquote, urlparse
import httpx
from bs4 import BeautifulSoup
import app as base

STOPWORDS={'best','top','near','me','in','the','a','an','and','or','of','for','to','at','with','local','nearby','new','york','ny','usa'}
GOOGLE_HOSTS=('google.com','maps.google.com','googleusercontent.com','gstatic.com','googleapis.com','goo.gl')
ORIG_WORKER=base.worker


def ad_from_text(text):
    t=' '+re.sub(r'\s+',' ',text or '').strip().lower()+' '
    return bool(re.search(r'(^|\s)(sponsored|ad|promoted)(\s|$|[·•:-])',t))


def cards(page):
    out=[]; seen=set(); links=page.locator('div[role="feed"] a[href*="/maps/place/"]')
    for i in range(min(links.count(),base.MAXR*6)):
        try:
            a=links.nth(i); href=a.get_attribute('href') or ''
            original=(a.get_attribute('aria-label') or a.inner_text(timeout=700) or '').strip()
            name=re.sub(r'^(Ad|Sponsored)\s*[·•:-]?\s*','',original,flags=re.I).strip()
            if not name or href in seen: continue
            seen.add(href)
            try: raw=a.locator("xpath=ancestor::div[@role='article'][1]").inner_text(timeout=900)
            except Exception: raw=original
            if ad_from_text(original+'\n'+raw): raw='[[AD=1]]\n'+raw
            out.append((name[:200],href,raw[:4000]))
            if len(out)>=base.MAXR: break
        except Exception: pass
    return out


def clean_external_url(href):
    if not href:return ''
    href=html.unescape(href.strip())
    if href.startswith('//'):href='https:'+href
    if href.startswith('/url?') or 'google.com/url?' in href:
        q=parse_qs(urlparse(href).query); href=(q.get('q') or q.get('url') or [''])[0]
    href=unquote(href)
    try:
        u=urlparse(href); host=(u.hostname or '').lower().lstrip('www.')
        if u.scheme not in ('http','https') or not host:return ''
        if any(host==h or host.endswith('.'+h) for h in GOOGLE_HOSTS):return ''
        return href
    except Exception:return ''


def website_from_profile(page):
    selectors=['a[data-item-id="authority"]','a[aria-label^="Website:"]','a[aria-label*="Website"]','a[data-tooltip*="website" i]','a[data-value="Website"]']
    for sel in selectors:
        try:
            loc=page.locator(sel)
            for i in range(min(loc.count(),6)):
                href=clean_external_url(loc.nth(i).get_attribute('href') or '')
                if href:return href
        except Exception:pass
    try:
        loc=page.locator('div[role="main"] a[href]'); candidates=[]
        for i in range(min(loc.count(),120)):
            a=loc.nth(i); href=clean_external_url(a.get_attribute('href') or '')
            if not href:continue
            label=((a.get_attribute('aria-label') or '')+' '+(a.inner_text(timeout=250) or '')).lower(); score=100 if 'website' in label else 0
            host=(urlparse(href).hostname or '').lower()
            if not any(s in host for s in ['facebook.com','instagram.com','yelp.com','tripadvisor.com','tiktok.com','x.com','twitter.com']):score+=10
            candidates.append((score,href))
        if candidates:return sorted(candidates,key=lambda x:-x[0])[0][1]
    except Exception:pass
    return ''


def base_site_audit(url):
    rr=httpx.get(url,timeout=15,follow_redirects=True,headers={'User-Agent':'Mozilla/5.0 (compatible; LocalSearchIntelligence/1.2)'})
    rr.raise_for_status(); final=str(rr.url); s=BeautifulSoup(rr.text,'html.parser')
    checks={'https':urlparse(final).scheme=='https','title':bool(s.title and s.title.get_text(strip=True)),'meta':bool(s.find('meta',attrs={'name':lambda x:x and x.lower()=='description'})),'h1':bool(s.find('h1')),'viewport':bool(s.find('meta',attrs={'name':lambda x:x and x.lower()=='viewport'})),'canonical':bool(s.find('link',rel=lambda x:x and 'canonical' in x)),'local_schema':'localbusiness' in rr.text.lower()}
    weights={'https':12,'title':14,'meta':12,'h1':12,'viewport':12,'canonical':10,'local_schema':28}
    return final,sum(weights[k] for k,v in checks.items() if v),checks


def enrich(name,url,lat,lon,tz):
    d={'name':name,'maps_url':url,'website':'','address':'','phone':'','rating':None,'reviews':None,'site_score':0,'site_json':'{}'}
    if not url:return d
    try:
        with base.browser(lat,lon,tz) as p:
            p.goto(url,wait_until='domcontentloaded',timeout=45000); base.dismiss(p); p.wait_for_timeout(2200)
            d['website']=website_from_profile(p)
            try:
                x=p.locator('button[data-item-id="address"]')
                if x.count():d['address']=(x.first.get_attribute('aria-label') or '').replace('Address: ','')
            except Exception:pass
            try:
                x=p.locator('button[data-item-id^="phone:tel:"]')
                if x.count():d['phone']=(x.first.get_attribute('aria-label') or '').replace('Phone: ','')
            except Exception:pass
            try:
                x=p.locator('div[role="main"] span[aria-hidden="true"]').filter(has_text=re.compile(r'^\d(?:\.\d)?$'))
                if x.count():d['rating']=float(x.first.inner_text().strip())
            except Exception:pass
            try:
                x=p.locator('button[aria-label*="reviews" i]')
                if x.count():
                    t=x.first.get_attribute('aria-label') or ''; m=re.search(r'([\d,]+)\s+reviews',t,re.I)
                    if m:d['reviews']=int(m.group(1).replace(',',''))
            except Exception:pass
    except Exception as e:d['site_json']=json.dumps({'profile_error':str(e)[:220]})
    if d['website']:
        try:
            d['website'],d['site_score'],checks=base_site_audit(d['website']); d['site_json']=json.dumps(checks)
        except Exception as e:d['site_json']=json.dumps({'website_found':True,'audit_error':str(e)[:220]})
    return d


def tokens(service):
    return [x for x in re.findall(r'[a-z0-9]+',(service or '').lower()) if len(x)>=3 and x not in STOPWORDS][:8]


def keyword_audit(url,service,existing):
    rr=httpx.get(url,timeout=15,follow_redirects=True,headers={'User-Agent':'Mozilla/5.0 (compatible; LocalSearchIntelligence/1.2)'})
    rr.raise_for_status(); s=BeautifulSoup(rr.text,'html.parser'); ts=tokens(service)
    title=s.title.get_text(' ',strip=True) if s.title else ''; h1=' '.join(x.get_text(' ',strip=True) for x in s.find_all('h1')[:3]); body=s.get_text(' ',strip=True)[:50000]
    rel=lambda text: bool(ts) and any(t in (text or '').lower() for t in ts)
    existing.update({'title_relevance':rel(title),'h1_relevance':rel(h1),'body_relevance':rel(body)})
    score=min(100,float(existing.get('_base_score',0))+15*existing['title_relevance']+12*existing['h1_relevance']+13*existing['body_relevance'])
    return score,existing


def worker(rid,q,loc):
    ORIG_WORKER(rid,q,loc)
    try:
        with base.db() as c:
            run=c.execute('select status,service from runs where id=?',(rid,)).fetchone()
            if not run or run['status']!='complete':return
            rows=c.execute('select bkey,website,site_score,site_json from details where run_id=?',(rid,)).fetchall()
        for r in rows:
            if not r['website']:continue
            try:
                checks=json.loads(r['site_json'] or '{}'); checks['_base_score']=float(r['site_score'] or 0)
                score,checks=keyword_audit(r['website'],run['service'],checks); checks.pop('_base_score',None)
                with base.db() as c:c.execute('update details set site_score=?,site_json=? where run_id=? and bkey=?',(score,json.dumps(checks),rid,r['bkey']))
            except Exception:pass
    except Exception:pass


def aggregate(rid,valid):
    with base.db() as c:
        obs=c.execute('select * from obs where run_id=?',(rid,)).fetchall(); ds={r['bkey']:r for r in c.execute('select * from details where run_id=?',(rid,)).fetchall()}
    g=defaultdict(list); ads=defaultdict(int); urls={}
    for o in obs:
        k=base.key(o['name']); g[k].append(o['rank']); raw=o['raw'] or ''; ads[k]+=int('[[AD=1]]' in raw or ad_from_text(raw)); urls[k]=(o['name'],o['maps_url'])
    arr=[]
    for k,rs in g.items():
        d=ds.get(k); name=d['name'] if d else urls[k][0]; website=d['website'] if d else ''; rating=d['rating'] if d else None; reviews=d['reviews'] if d else None; site=float(d['site_score'] or 0) if d else 0
        top5=100*sum(r<=5 for r in rs)/max(valid,1); top3=100*sum(r<=3 for r in rs)/max(valid,1); vis=100*sum(1/r for r in rs)/max(valid,1); stab=max(0,100-min(100,pstdev(rs)*12)) if len(rs)>1 else 100
        rat=max(0,min(1,((rating or 0)-3)/2)); rev=min(1,math.log1p(max(0,reviews or 0))/math.log1p(1000)); strength=100*(.45*rat+.55*rev); opp=strength*.30+(100-site)*.35+(100-min(100,top5))*.25+max(0,min(100,100-vis))*.10
        try:checks=json.loads(d['site_json'] or '{}') if d else {}
        except Exception:checks={}
        arr.append({'key':k,'name':name,'website':website,'address':d['address'] if d else '','phone':d['phone'] if d else '','rating':rating,'reviews':reviews,'site_score':round(site,1),'site_checks':checks,'top5':round(top5,1),'top3':round(top3,1),'median':median(rs),'avg':round(sum(rs)/len(rs),2),'coverage':round(100*len(rs)/max(valid,1),1),'visibility':round(vis,1),'stability':round(stab,1),'opportunity':round(opp,1),'maps_url':d['maps_url'] if d else urls[k][1],'appearances':len(rs),'ad_hits':ads[k],'ad_share':round(100*ads[k]/max(len(rs),1),1)})
    return sorted(arr,key=lambda x:(-x['opportunity'],x['median']))


def med(arr,field):
    v=[x[field] for x in arr if x.get(field) not in (None,'')]; return median(v) if v else 0


def business_link(x):
    n=html.escape(x['name']); u=html.escape(x.get('maps_url') or '',quote=True)
    return f"<a class='biz' target='_blank' rel='noopener' href='{u}'>{n} ↗</a>" if u else n


def explanation_html(x,arr,valid):
    mr=med(arr,'reviews'); ms=med([a for a in arr if a.get('website')],'site_score'); hits=round(x['top5']/100*valid); b=[]
    b.append(f"<li><b>Consistency:</b> Top 5 in about {hits} of {valid} clean searches ({x['top5']}%), median position #{x['median']}.</li>")
    if x['ad_hits']:
        b.append(f"<li><b>Google Ads observed:</b> Sponsored/Ad was captured in {x['ad_hits']} of {x['appearances']} appearances ({x['ad_share']}%). This can boost placement on those pages, but it does not buy the organic local rank.</li>")
    else:b.append("<li><b>Google Ads:</b> no Sponsored/Ad label was captured for this business in this scan. That means paid placement was not the visible reason in the tested pages; it does not prove the business never advertises.</li>")
    if x.get('rating') is not None or x.get('reviews') is not None:
        rt=f"{x['rating']:.1f}★" if x.get('rating') is not None else 'rating unavailable'; rc=f"{x['reviews']:,} reviews" if x.get('reviews') is not None else 'review count unavailable'; comp=''
        if x.get('reviews') is not None and mr:comp=f" vs market median ~{mr:,.0f}"
        b.append(f"<li><b>Reviews / prominence:</b> {rt}, {rc}{comp}. Strong review volume and positive ratings can support local prominence.</li>")
    checks=x.get('site_checks') or {}
    if x.get('website'):
        positives=[]
        if checks.get('title_relevance'):positives.append('service terms in title')
        if checks.get('h1_relevance'):positives.append('service terms in H1')
        if checks.get('local_schema'):positives.append('local business schema')
        if checks.get('viewport'):positives.append('mobile setup')
        extra=', '.join(positives[:4]) if positives else 'basic technical signals only'
        b.append(f"<li><b>Website / SEO:</b> website verified; measured SEO score {x['site_score']:.0f}/100 ({html.escape(extra)}). Market website median is about {ms:.0f}/100.</li>")
    else:b.append("<li><b>Website / SEO:</b> no website could be verified from the Google Business Profile after multiple detection methods, so website quality is unlikely to explain this ranking.</li>")
    complete=sum(bool(x.get(k)) for k in ('website','address','phone')); b.append(f"<li><b>Profile completeness:</b> {complete}/3 core contact signals verified (website, address, phone).</li>")
    if x['coverage']>=75:b.append(f"<li><b>Location resilience:</b> appeared somewhere in {x['coverage']}% of valid geo-grid searches, so visibility is not dependent on one exact point.</li>")
    likely=[]
    if (x.get('reviews') or 0)>=max(100,mr*1.25 if mr else 100) and (x.get('rating') or 0)>=4.4:likely.append('strong reviews/prominence')
    if x.get('site_score',0)>=max(70,ms+8 if ms else 70):likely.append('stronger website/SEO signals')
    if x['ad_hits']:likely.append('paid visibility on some searches')
    if x['top5']>=70:likely.append('strong relevance/location consistency')
    if not likely:likely=['a combination of relevance, distance and broader prominence signals']
    b.append('<li><b>Bottom line:</b> strongest measured contributors: '+html.escape(', '.join(likely))+'. Usually it is a combination, not one single reason.</li>')
    return "<details class='why'><summary>Why is this business ranking?</summary><div class='whybox'><ul>"+''.join(b)+"</ul><p class='note'><b>Important:</b> observable evidence, not Google's private formula. Google documents relevance, distance and prominence as the main local factors; ads are separate.</p></div></details>"

base.cards=cards
base.enrich=enrich
base.aggregate=aggregate
base.worker=worker
