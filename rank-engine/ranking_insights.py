from __future__ import annotations
import html, math, re
from collections import defaultdict
from statistics import median, pstdev
import app as base
import enhancements as enh

_BASE_AGGREGATE = enh.aggregate


def _pct_rank(value, values):
    vals=[v for v in values if v is not None]
    if value is None or not vals:
        return None
    return round(100.0*sum(v <= value for v in vals)/len(vals),1)


def _haversine_miles(lat1, lon1, lat2, lon2):
    r=3958.7613
    p1=math.radians(lat1); p2=math.radians(lat2)
    dp=math.radians(lat2-lat1); dl=math.radians(lon2-lon1)
    a=math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return 2*r*math.asin(math.sqrt(a))


def _maps_coords(url):
    if not url:
        return None
    for pat in (r'/@(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)', r'!3d(-?\d+(?:\.\d+)?)!4d(-?\d+(?:\.\d+)?)'):
        m=re.search(pat,url)
        if m:
            try:return float(m.group(1)),float(m.group(2))
            except Exception:return None
    return None


def _head_to_head(a_key,b_key,exec_ranks,valid_execs):
    wins=losses=ties=0
    a=exec_ranks.get(a_key,{})
    b=exec_ranks.get(b_key,{})
    for ex in valid_execs:
        ra=a.get(ex); rb=b.get(ex)
        if ra is None and rb is None:continue
        if ra is None:losses+=1
        elif rb is None:wins+=1
        elif ra < rb:wins+=1
        elif rb < ra:losses+=1
        else:ties+=1
    comp=wins+losses+ties
    return {'wins':wins,'losses':losses,'ties':ties,'comparable':comp,'win_rate':round(100*wins/comp,1) if comp else None}


def aggregate(rid,valid):
    with base.db() as c:
        obs=c.execute('select * from obs where run_id=? order by point,rep,rank,id',(rid,)).fetchall()
        scans=c.execute('select point,rep,status from scans where run_id=?',(rid,)).fetchall()
        details={r['bkey']:r for r in c.execute('select * from details where run_id=?',(rid,)).fetchall()}
    valid_execs={(int(s['point']),int(s['rep'])) for s in scans if s['status']=='ok'}
    if not valid_execs:
        valid_execs={(int(o['point']),int(o['rep'])) for o in obs}
    valid_n=len(valid_execs) or valid or 1
    point_valid=defaultdict(set)
    for p,r in valid_execs:point_valid[p].add(r)
    valid_points=set(point_valid)

    rows_by_exec=defaultdict(list)
    exec_coord={}
    for o in obs:
        ex=(int(o['point']),int(o['rep'])); rows_by_exec[ex].append(o)
        exec_coord[ex]=(float(o['lat']),float(o['lon']))

    exec_ranks=defaultdict(dict); ads=defaultdict(int); urls={}; names={}
    for ex,rows in rows_by_exec.items():
        organic_pos=0
        for o in sorted(rows,key=lambda z:int(z['rank'])):
            k=base.key(o['name']); names[k]=o['name']; urls[k]=o['maps_url']
            raw=o['raw'] or ''
            is_ad='[[AD=1]]' in raw or enh.ad_from_text(raw)
            if is_ad:
                ads[k]+=1
                continue
            organic_pos+=1
            if k not in exec_ranks or ex not in exec_ranks[k]:
                exec_ranks[k][ex]=organic_pos

    all_keys=set(exec_ranks) | set(details)
    arr=[]
    for k in all_keys:
        rank_map=exec_ranks.get(k,{})
        ranks=list(rank_map.values())
        if not ranks:
            continue
        d=details.get(k)
        name=d['name'] if d else names.get(k,k)
        website=d['website'] if d else ''
        rating=d['rating'] if d else None; reviews=d['reviews'] if d else None
        site=float(d['site_score'] or 0) if d else 0
        try:checks=__import__('json').loads(d['site_json'] or '{}') if d else {}
        except Exception:checks={}
        top5=100*sum(r<=5 for r in ranks)/valid_n
        top3=100*sum(r<=3 for r in ranks)/valid_n
        vis=100*sum(1/r for r in ranks)/valid_n
        stab=max(0,100-min(100,pstdev(ranks)*12)) if len(ranks)>1 else 100
        rat=max(0,min(1,((rating or 0)-3)/2)); rev=min(1,math.log1p(max(0,reviews or 0))/math.log1p(1000))
        strength=100*(.45*rat+.55*rev)
        opp=strength*.30+(100-site)*.35+(100-min(100,top5))*.25+max(0,min(100,100-vis))*.10
        arr.append({'key':k,'name':name,'website':website,'address':d['address'] if d else '',
                    'phone':d['phone'] if d else '','rating':rating,'reviews':reviews,
                    'site_score':round(site,1),'site_checks':checks,'top5':round(top5,1),
                    'top3':round(top3,1),'median':median(ranks),'avg':round(sum(ranks)/len(ranks),2),
                    'coverage':round(100*len(ranks)/valid_n,1),'visibility':round(vis,1),
                    'stability':round(stab,1),'opportunity':round(opp,1),
                    'maps_url':d['maps_url'] if d else urls.get(k,''),'appearances':len(ranks),
                    'ad_hits':ads[k],'ad_share':round(100*ads[k]/valid_n,1)})
    if not arr:return arr

    market_reviews=[x.get('reviews') for x in arr if x.get('reviews') is not None]
    market_ratings=[x.get('rating') for x in arr if x.get('rating') is not None]
    market_sites=[x.get('site_score') for x in arr if x.get('website')]
    market_top5=[x.get('top5') for x in arr]
    market_visibility=[x.get('visibility') for x in arr]
    med_reviews=median(market_reviews) if market_reviews else None
    med_rating=median(market_ratings) if market_ratings else None
    med_site=median(market_sites) if market_sites else None
    med_top5=median(market_top5) if market_top5 else None

    leaders=sorted(arr,key=lambda x:(-x.get('top5',0),-x.get('top3',0),-x.get('coverage',0),x.get('median',999),-x.get('visibility',0)))
    leader_keys=[x['key'] for x in leaders]
    leader_name={x['key']:x['name'] for x in leaders}

    for x in arr:
        k=x['key']; ranks=list(exec_ranks.get(k,{}).values())
        x['valid_searches']=valid_n
        x['rank1_count']=sum(r==1 for r in ranks); x['rank1_pct']=round(100*x['rank1_count']/valid_n,1)
        x['top3_count']=sum(r<=3 for r in ranks); x['top5_count']=sum(r<=5 for r in ranks)
        x['best_rank']=min(ranks) if ranks else None; x['worst_shown_rank']=max(ranks) if ranks else None
        x['visibility_rank']=leader_keys.index(k)+1 if k in leader_keys else None
        x['market_businesses']=len(arr)
        x['review_percentile']=_pct_rank(x.get('reviews'),market_reviews)
        x['rating_percentile']=_pct_rank(x.get('rating'),market_ratings)
        x['site_percentile']=_pct_rank(x.get('site_score'),market_sites) if x.get('website') else None
        x['top5_percentile']=_pct_rank(x.get('top5'),market_top5)
        x['visibility_percentile']=_pct_rank(x.get('visibility'),market_visibility)
        x['market_median_reviews']=med_reviews; x['market_median_rating']=med_rating; x['market_median_site']=med_site; x['market_median_top5']=med_top5
        x['review_multiple']=round(x['reviews']/med_reviews,2) if x.get('reviews') is not None and med_reviews not in (None,0) else None
        x['site_delta']=round(x.get('site_score',0)-med_site,1) if x.get('website') and med_site is not None else None
        x['top5_delta']=round(x.get('top5',0)-med_top5,1) if med_top5 is not None else None

        majority_top3=majority_top5=rank1_points=0
        for p in valid_points:
            reps=point_valid[p]; prs=[exec_ranks.get(k,{}).get((p,r)) for r in reps]
            top3=sum(v is not None and v<=3 for v in prs); top5=sum(v is not None and v<=5 for v in prs)
            threshold=max(1,math.ceil(len(reps)/2))
            if top3>=threshold:majority_top3+=1
            if top5>=threshold:majority_top5+=1
            if any(v==1 for v in prs if v is not None):rank1_points+=1
        x['valid_points']=len(valid_points); x['majority_top3_points']=majority_top3; x['majority_top5_points']=majority_top5; x['rank1_points']=rank1_points

        rival=next((rk for rk in leader_keys if rk!=k),None)
        if rival:
            h=_head_to_head(k,rival,exec_ranks,valid_execs); h['rival_name']=leader_name.get(rival,'another leading competitor'); x['head_to_head']=h
        else:x['head_to_head']=None

        coords=_maps_coords(x.get('maps_url'))
        x['business_coords']=coords
        if coords and exec_coord:
            samples=[]
            for ex in valid_execs:
                pt=exec_coord.get(ex)
                if not pt:continue
                d=_haversine_miles(pt[0],pt[1],coords[0],coords[1]); samples.append((d,exec_ranks.get(k,{}).get(ex)))
            samples.sort(key=lambda z:z[0])
            if len(samples)>=4:
                split=max(1,len(samples)//2); near=samples[:split]; far=samples[split:]
                def t5(s):return round(100*sum(r is not None and r<=5 for _,r in s)/len(s),1) if s else None
                near5=t5(near); far5=t5(far)
                x['proximity']={'near_top5':near5,'far_top5':far5,'delta':round(near5-far5,1),'near_median_miles':round(median([d for d,_ in near]),2),'far_median_miles':round(median([d for d,_ in far]),2)}
            else:x['proximity']=None
        else:x['proximity']=None
    return sorted(arr,key=lambda x:(-x['opportunity'],x['median']))


def _feature_summary(checks):
    good=[]; weak=[]
    names=[('title_relevance','service terms in page title'),('h1_relevance','service terms in H1'),('body_relevance','service terms in page content'),('local_schema','LocalBusiness structured data'),('viewport','mobile viewport'),('canonical','canonical tag'),('meta','meta description')]
    for key,label in names:
        if key in checks:
            (good if checks.get(key) else weak).append(label)
    return good,weak


def explanation_html(x,arr,valid):
    valid_n=x.get('valid_searches') or valid or 1
    bullets=[]; negatives=[]
    n=x.get('market_businesses') or len(arr)
    vr=x.get('visibility_rank')
    if vr:
        bullets.append(f"<li><b>Actual ranking dominance:</b> #{vr} of {n} businesses by this scan's local visibility. It ranked <b>#1 in {x.get('rank1_count',0)} of {valid_n}</b> valid searches ({x.get('rank1_pct',0)}%), Top 3 in {x.get('top3_count',0)}/{valid_n}, and Top 5 in {x.get('top5_count',0)}/{valid_n}. Its median shown position was #{x.get('median')}.</li>")

    vp=x.get('valid_points',0)
    if vp:
        bullets.append(f"<li><b>Geographic strength:</b> it was Top 3 in the majority of repetitions at <b>{x.get('majority_top3_points',0)} of {vp}</b> grid points and Top 5 at {x.get('majority_top5_points',0)} of {vp}. It reached #1 at least once at {x.get('rank1_points',0)} grid points.</li>")

    h=x.get('head_to_head')
    if h and h.get('comparable'):
        rate=h.get('win_rate') or 0
        if rate>=55:
            bullets.append(f"<li><b>Head-to-head proof:</b> against <b>{html.escape(h['rival_name'])}</b>, it appeared above that competitor in <b>{h['wins']} of {h['comparable']}</b> comparable searches ({rate}%).</li>")
        else:
            negatives.append(f"It did not consistently beat {html.escape(h['rival_name'])} head-to-head ({rate}% win rate), so its top position is not universal across the grid.")

    reviews=x.get('reviews'); mr=x.get('market_median_reviews'); rp=x.get('review_percentile'); rating=x.get('rating')
    if reviews is not None and mr is not None:
        if reviews>=mr*1.25 and (rating or 0)>=4.3:
            mult=x.get('review_multiple')
            bullets.append(f"<li><b>Real review advantage:</b> {(f'{rating:.1f}★' if rating is not None else 'rating unavailable')} with <b>{reviews:,} reviews</b> versus a market median of {mr:,.0f}. That's about <b>{mult:.1f}× the typical review volume</b> in this scan and roughly the {rp:.0f}th percentile.</li>")
        elif reviews<mr*.75:
            negatives.append(f"Reviews are not its main advantage: {reviews:,} reviews versus a market median of {mr:,.0f}.")
        else:
            bullets.append(f"<li><b>Reviews:</b> {(f'{rating:.1f}★' if rating is not None else 'rating unavailable')} with {reviews:,} reviews, close to the market median of {mr:,.0f}; reviews alone do not explain the ranking.</li>")

    if x.get('website'):
        ms=x.get('market_median_site'); sd=x.get('site_delta'); sp=x.get('site_percentile'); checks=x.get('site_checks') or {}; good,weak=_feature_summary(checks)
        good_text=', '.join(good[:4]) if good else 'basic technical checks passed'
        if ms is not None and sd is not None and sd>=8:
            bullets.append(f"<li><b>Website/SEO advantage:</b> measured site score <b>{x.get('site_score',0):.0f}/100</b> versus market median {ms:.0f}/100 (+{sd:.0f}). It is around the {sp:.0f}th percentile; verified strengths include {html.escape(good_text)}.</li>")
        elif ms is not None and sd is not None and sd<=-8:
            negatives.append(f"Its website is actually weaker than the market benchmark ({x.get('site_score',0):.0f}/100 vs {ms:.0f}/100), so the website is unlikely to be the primary reason it ranks this well.")
        else:
            bullets.append(f"<li><b>Website/SEO:</b> measured site score {x.get('site_score',0):.0f}/100, roughly in line with the market benchmark. Verified signals: {html.escape(good_text)}.</li>")
        if weak:
            negatives.append('Website weaknesses still detected: '+html.escape(', '.join(weak[:3]))+'.')
    else:
        negatives.append('No official website was verified from the profile, so a strong website cannot be credited as the reason for this ranking.')

    p=x.get('proximity')
    if p:
        if p['delta']>=20:
            bullets.append(f"<li><b>Distance/proximity effect:</b> Top 5 coverage was <b>{p['near_top5']}%</b> in the nearer half of test searches versus {p['far_top5']}% in the farther half (a {p['delta']}-point gap). Proximity appears to be a meaningful contributor in this scan.</li>")
        elif abs(p['delta'])<10 and x.get('top5',0)>=50:
            bullets.append(f"<li><b>Not just proximity:</b> Top 5 coverage stayed similar near and farther from the business ({p['near_top5']}% vs {p['far_top5']}%). Its visibility travels across the market, so distance alone does not explain the result.</li>")

    if x.get('ad_hits',0):
        bullets.append(f"<li><b>Paid visibility:</b> Sponsored/Ad labeling was observed {x['ad_hits']} times. That adds exposure on those pages, but the organic rank figures above are evaluated separately.</li>")
    else:
        negatives.append('No Sponsored/Ad label was observed, so paid placement was not the visible explanation in this audit.')

    factors=[]
    if rp is not None and rp>=75 and (rating or 0)>=4.3:factors.append('above-market review prominence')
    if x.get('site_percentile') is not None and x.get('site_percentile')>=75 and x.get('site_score',0)>=60:factors.append('above-market website/SEO signals')
    if p and p['delta']>=20:factors.append('a measurable proximity advantage')
    if x.get('majority_top3_points',0)>=max(1,round((x.get('valid_points') or 1)*.6)):factors.append('broad geographic ranking consistency')
    if h and h.get('win_rate') is not None and h['win_rate']>=65:factors.append('strong head-to-head performance')
    if x.get('ad_hits',0):factors.append('additional sponsored exposure')
    if not factors:factors=['a combination of local relevance, distance and broader prominence signals that are not fully observable from public data']

    bottom=f"<div class='bottomline'><b>Most defensible conclusion:</b> For this business, the strongest measured explanation is {html.escape(', '.join(factors))}. This is based on this scan's comparative data—not a generic SEO checklist and not a claim to know Google's private algorithm.</div>"
    notwhy=''
    if negatives:
        notwhy="<div class='notwhy'><b>What does NOT fully explain the ranking:</b><ul>"+''.join(f'<li>{n}</li>' for n in negatives[:4])+"</ul></div>"
    return "<details class='why'><summary>Why is this business actually on top?</summary><div class='whybox'><ul>"+''.join(bullets)+"</ul>"+bottom+notwhy+"<p class='note'>Evidence is comparative to businesses and search points in this audit. Google does not publish a business-by-business causal explanation of its ranking decisions.</p></div></details>"


def improved_keyword_audit(url,service,existing):
    import httpx
    from bs4 import BeautifulSoup
    rr=httpx.get(url,timeout=15,follow_redirects=True,headers={'User-Agent':'Mozilla/5.0 (compatible; LocalSearchIntelligence/2.0)'})
    rr.raise_for_status(); soup=BeautifulSoup(rr.text,'html.parser')
    ts=enh.tokens(service)
    title=soup.title.get_text(' ',strip=True) if soup.title else ''
    h1=' '.join(x.get_text(' ',strip=True) for x in soup.find_all('h1')[:3])
    body=soup.get_text(' ',strip=True)[:50000]
    def coverage(text):
        if not ts:return 0.0,[]
        low=(text or '').lower(); matched=[t for t in ts if t in low]
        return len(matched)/len(ts),matched
    tc,tm=coverage(title); hc,hm=coverage(h1); bc,bm=coverage(body)
    existing.update({
        'service_terms':ts,
        'title_match_pct':round(tc*100,1),'h1_match_pct':round(hc*100,1),'body_match_pct':round(bc*100,1),
        'title_matches':tm,'h1_matches':hm,'body_matches':bm,
        'title_relevance':tc>=0.5 if ts else False,'h1_relevance':hc>=0.5 if ts else False,'body_relevance':bc>=0.5 if ts else False,
        'title_text':title[:180],'h1_text':h1[:180]
    })
    technical=float(existing.get('_base_score',0))
    relevance=100*(0.4*tc+0.3*hc+0.3*bc)
    existing['technical_score']=round(technical,1); existing['service_relevance_score']=round(relevance,1)
    return round(0.60*technical+0.40*relevance,1),existing

def patch():
    enh.aggregate=aggregate
    enh.explanation_html=explanation_html
    enh.keyword_audit=improved_keyword_audit
