from __future__ import annotations
import json, re
import app as base
import enhancements as enh

_ORIG_WORKER = enh.worker


def _text(el, timeout=500):
    try:
        return (el.inner_text(timeout=timeout) or '').strip()
    except Exception:
        return ''


def _profile_signals(page):
    out={
        'primary_category':'',
        'hours_present':False,
        'profile_signal_source':'google_maps_profile',
    }

    # Google's Maps DOM changes often, so use a fallback chain and only accept
    # short category-like labels. Failure means "not captured", never "no category".
    selectors=[
        'button[jsaction*="rating.category"]',
        'button[jsaction*="category"]',
        'button.DkEaL',
        'div[role="main"] button.DkEaL',
    ]
    seen=[]
    for sel in selectors:
        try:
            loc=page.locator(sel)
            for i in range(min(loc.count(),8)):
                t=_text(loc.nth(i))
                if not t: continue
                t=' '.join(t.split())
                low=t.lower()
                if len(t)>90 or re.search(r'\d',t): continue
                if any(bad in low for bad in ('review','website','direction','save','share','call','address','hours','menu')): continue
                if t not in seen: seen.append(t)
        except Exception:
            pass
    if seen:
        out['primary_category']=seen[0]
        out['category_candidates']=seen[:5]

    hour_selectors=[
        'button[data-item-id^="oh"]',
        'button[aria-label*="Hours" i]',
        'div[aria-label*="Hours" i]',
        'table[aria-label*="Hours" i]',
    ]
    for sel in hour_selectors:
        try:
            if page.locator(sel).count():
                out['hours_present']=True
                break
        except Exception:
            pass

    return out


def enrich(name,url,lat,lon,tz):
    d={'name':name,'maps_url':url,'website':'','address':'','phone':'','rating':None,'reviews':None,'site_score':0,'site_json':'{}'}
    profile_meta={}
    if not url:
        d['site_json']=json.dumps(profile_meta)
        return d
    try:
        with base.browser(lat,lon,tz) as p:
            p.goto(url,wait_until='domcontentloaded',timeout=45000)
            base.dismiss(p)
            p.wait_for_timeout(2200)
            profile_meta=_profile_signals(p)
            d['website']=enh.website_from_profile(p)
            try:
                x=p.locator('button[data-item-id="address"]')
                if x.count(): d['address']=(x.first.get_attribute('aria-label') or '').replace('Address: ','')
            except Exception: pass
            try:
                x=p.locator('button[data-item-id^="phone:tel:"]')
                if x.count(): d['phone']=(x.first.get_attribute('aria-label') or '').replace('Phone: ','')
            except Exception: pass
            try:
                x=p.locator('div[role="main"] span[aria-hidden="true"]').filter(has_text=re.compile(r'^\d(?:\.\d)?$'))
                if x.count(): d['rating']=float(x.first.inner_text().strip())
            except Exception: pass
            try:
                x=p.locator('button[aria-label*="reviews" i]')
                if x.count():
                    t=x.first.get_attribute('aria-label') or ''
                    m=re.search(r'([\d,]+)\s+reviews',t,re.I)
                    if m: d['reviews']=int(m.group(1).replace(',',''))
            except Exception: pass
    except Exception as e:
        profile_meta['profile_error']=str(e)[:220]

    if d['website']:
        try:
            d['website'],d['site_score'],checks=enh.base_site_audit(d['website'])
            profile_meta.update(checks)
        except Exception as e:
            profile_meta.update({'website_found':True,'audit_error':str(e)[:220]})
    d['site_json']=json.dumps(profile_meta)
    return d


def _exact_category_overlap(service, category):
    ts=enh.tokens(service)
    if not ts or not category:
        return None,[]
    low=category.lower()
    matched=[t for t in ts if re.search(rf'\b{re.escape(t)}\b',low)]
    return round(100*len(matched)/len(ts),1),matched


def worker(rid,q,loc):
    _ORIG_WORKER(rid,q,loc)
    try:
        with base.db() as c:
            run=c.execute('select status,service from runs where id=?',(rid,)).fetchone()
            if not run or run['status']!='complete': return
            rows=c.execute('select bkey,site_json from details where run_id=?',(rid,)).fetchall()
        for r in rows:
            try:
                checks=json.loads(r['site_json'] or '{}')
            except Exception:
                checks={}
            category=checks.get('primary_category') or ''
            overlap,matched=_exact_category_overlap(run['service'],category)
            checks['category_exact_overlap_pct']=overlap
            checks['category_exact_matches']=matched
            checks['service_terms']=enh.tokens(run['service'])
            checks['profile_core_fields_captured']=None  # calculated from detail columns at report time
            with base.db() as c:
                c.execute('update details set site_json=? where run_id=? and bkey=?',(json.dumps(checks),rid,r['bkey']))
    except Exception as e:
        print(f'[profile-signals:{rid}] postprocess error {e!r}',flush=True)


def patch():
    enh.enrich=enrich
    base.enrich=enrich
    enh.worker=worker
    base.worker=worker
