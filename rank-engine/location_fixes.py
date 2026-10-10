from __future__ import annotations
import re, time
import app as base

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
}

def normalize_location_text(place: str) -> str:
    text=' '.join((place or '').strip().split())
    for joined, proper in _JOINED_US_NAMES.items():
        text=re.sub(rf'(?i)(?<![A-Za-z]){re.escape(joined)}(?![A-Za-z])',proper,text)
    text=re.sub(r'\bN\.Y\.\b','NY',text,flags=re.I)
    text=re.sub(r'\bN\.J\.\b','NJ',text,flags=re.I)
    return ' '.join(text.split())

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

base.geocode=robust_geocode
base.worker=logged_worker
