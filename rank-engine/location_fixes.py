from __future__ import annotations
import re, time
import app as base

_ORIG_PARSE = base.parse
_ORIG_GEOCODE = base.geocode
_ORIG_WORKER = base.worker

_JOINED_US_NAMES = {
    'newyork':'New York', 'newjersey':'New Jersey', 'newmexico':'New Mexico',
    'newhampshire':'New Hampshire', 'northcarolina':'North Carolina',
    'southcarolina':'South Carolina', 'northdakota':'North Dakota',
    'southdakota':'South Dakota', 'westvirginia':'West Virginia',
    'rhodeisland':'Rhode Island', 'losangeles':'Los Angeles',
    'sanfrancisco':'San Francisco', 'fortworth':'Fort Worth',
    'washingtondc':'Washington DC', 'neworleans':'New Orleans',
    'longislandcity':'Long Island City', 'saltlakecity':'Salt Lake City',
    'kansascity':'Kansas City', 'oklahomacity':'Oklahoma City',
}

_STATE_NAMES = {
    'alabama','alaska','arizona','arkansas','california','colorado','connecticut','delaware',
    'florida','georgia','hawaii','idaho','illinois','indiana','iowa','kansas','kentucky',
    'louisiana','maine','maryland','massachusetts','michigan','minnesota','mississippi',
    'missouri','montana','nebraska','nevada','new hampshire','new jersey','new mexico',
    'new york','north carolina','north dakota','ohio','oklahoma','oregon','pennsylvania',
    'rhode island','south carolina','south dakota','tennessee','texas','utah','vermont',
    'virginia','washington','west virginia','wisconsin','wyoming','district of columbia'
}
_STATE_ABBRS = {
    'al','ak','az','ar','ca','co','ct','de','fl','ga','hi','id','il','in','ia','ks','ky','la',
    'me','md','ma','mi','mn','ms','mo','mt','ne','nv','nh','nj','nm','ny','nc','nd','oh','ok',
    'or','pa','ri','sc','sd','tn','tx','ut','vt','va','wa','wv','wi','wy','dc'
}

def normalize_location_text(place: str) -> str:
    text=' '.join((place or '').strip().split())
    for joined, proper in _JOINED_US_NAMES.items():
        text=re.sub(rf'(?i)(?<![A-Za-z]){re.escape(joined)}(?![A-Za-z])',proper,text)
    text=re.sub(r'\bN\.Y\.\b','NY',text,flags=re.I)
    text=re.sub(r'\bN\.J\.\b','NJ',text,flags=re.I)
    return ' '.join(text.split())

def _normalized_words(text: str):
    return [re.sub(r'[^a-z0-9]+','',x.lower()) for x in normalize_location_text(text).split() if re.sub(r'[^a-z0-9]+','',x.lower())]

def _state_suffix_len(words):
    for n in (3,2,1):
        if len(words) < n:
            continue
        phrase=' '.join(words[-n:]).lower().strip(' ,.')
        if phrase in _STATE_NAMES or phrase in _STATE_ABBRS:
            return n
    return 0

def _candidate_matches_locality(candidate: str, result: dict, state_len: int) -> bool:
    cand=_normalized_words(candidate)
    locality=cand[:-state_len] if state_len else cand
    display=' '.join(_normalized_words(result.get('display','')))
    meaningful=[t for t in locality if len(t) >= 3 and t not in {'the','and','for'}]
    return bool(meaningful) and all(t in display for t in meaningful)

def _infer_suffix_location(query: str):
    text=normalize_location_text(query)
    words=text.split()
    if len(words) < 2:
        return None
    state_len=_state_suffix_len(words)

    if state_len:
        max_city=min(5, len(words)-state_len-1)
        for city_len in range(max_city,0,-1):
            split=len(words)-state_len-city_len
            if split < 1:
                continue
            candidate=' '.join(words[split:])
            try:
                result=_ORIG_GEOCODE(candidate)
                if _candidate_matches_locality(candidate,result,state_len):
                    return ' '.join(words[:split]).strip(), candidate
            except Exception:
                pass
            time.sleep(1.02)
        return None

    max_loc=min(4,len(words)-1)
    for loc_len in range(max_loc,0,-1):
        split=len(words)-loc_len
        if split < 1:
            continue
        candidate=' '.join(words[split:])
        try:
            result=_ORIG_GEOCODE(candidate)
            if _candidate_matches_locality(candidate,result,0):
                return ' '.join(words[:split]).strip(), candidate
        except Exception:
            pass
        time.sleep(1.02)
    return None

def smart_parse(q: str):
    q=' '.join((q or '').strip().split())
    normalized=normalize_location_text(q)
    matches=list(re.finditer(r'\s+in\s+',normalized,re.I))
    if matches:
        m=matches[-1]
        service=normalized[:m.start()].strip()
        loc=normalized[m.end():].strip()
        print(f"[parse] explicit-in query={q!r} service={service!r} location={loc!r}",flush=True)
        return service,loc

    inferred=_infer_suffix_location(normalized)
    if inferred:
        service,loc=inferred
        print(f"[parse] inferred query={q!r} service={service!r} location={loc!r}",flush=True)
        return service,loc

    service,loc=_ORIG_PARSE(normalized)
    print(f"[parse] fallback query={q!r} service={service!r} location={loc!r}",flush=True)
    return service,loc

def location_variants(place: str):
    normalized=normalize_location_text(place)
    candidates=[]
    def add(x):
        x=' '.join((x or '').strip(' ,').split())
        if x and x.lower() not in {c.lower() for c in candidates}: candidates.append(x)
    add(normalized)
    m=re.match(r'^(.*?)(?:,|\s)+(New York|New Jersey|California|Texas|Florida|Illinois|Georgia|Pennsylvania|Massachusetts|Ohio|Virginia|Arizona|Washington)$',normalized,re.I)
    if m and m.group(1).strip(): add(f"{m.group(1).strip()}, {m.group(2)}, USA")
    if re.search(r'\bNew York\b',normalized,re.I) and not re.search(r'\b(New York City|NYC)\b',normalized,re.I):
        before=re.sub(r'(?i)[,\s]+New York\s*$','',normalized).strip(' ,')
        if before: add(f"{before}, New York City, New York, USA")
    if not re.search(r'\b(USA|United States)\b',normalized,re.I): add(normalized+', USA')
    return candidates[:4]

def robust_geocode(place: str):
    attempts=[]
    for i,candidate in enumerate(location_variants(place)):
        if i: time.sleep(1.05)
        try:
            result=_ORIG_GEOCODE(candidate)
            print(f"[geocode] input={place!r} resolved={candidate!r} -> {result.get('display','')}",flush=True)
            return result
        except Exception as e:
            attempts.append(f"{candidate}: {e}")
            print(f"[geocode] attempt failed input={place!r} candidate={candidate!r}: {e}",flush=True)
    raise RuntimeError(f"Could not locate '{place}' after normalization. Tried: " + ' | '.join(attempts))

def logged_worker(rid: str, q: str, loc: str):
    print(f"[scan:{rid}] START query={q!r} location={loc!r} variants={location_variants(loc)!r}",flush=True)
    _ORIG_WORKER(rid,q,loc)
    try:
        with base.db() as c:
            r=c.execute('select status,error,display_location,planned,valid,failed from runs where id=?',(rid,)).fetchone()
        if r:
            print(f"[scan:{rid}] END status={r['status']} display={r['display_location']!r} planned={r['planned']} valid={r['valid']} failed={r['failed']} error={r['error']!r}",flush=True)
    except Exception as e:
        print(f"[scan:{rid}] LOGGING_ERROR {e!r}",flush=True)

base.parse=smart_parse
base.geocode=robust_geocode
base.worker=logged_worker
