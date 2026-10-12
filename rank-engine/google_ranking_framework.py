from __future__ import annotations
import html
import app as base
import enhancements as enh

_BASE_AGGREGATE = enh.aggregate

GOOGLE_LOCAL_RANKING_DOC='https://support.google.com/business/answer/7091'
GOOGLE_CATEGORY_DOC='https://support.google.com/business/answer/7249669'
GOOGLE_LOCAL_INFO_DOC='https://support.google.com/local-listings/answer/9851099'
GOOGLE_MAPS_ADS_DOC='https://support.google.com/google-ads/answer/16388390'


def _fmt_pct(v):
    return 'Unavailable' if v is None else f'{v:.1f}%'


def _coverage_label(pct):
    if pct >= 85: return 'High'
    if pct >= 60: return 'Moderate'
    return 'Limited'


def _safe(v):
    return html.escape(str(v))


def aggregate(rid,valid):
    arr=_BASE_AGGREGATE(rid,valid)
    if not arr:
        return arr
    with base.db() as c:
        run=c.execute('select query,service,planned,valid,failed from runs where id=?',(rid,)).fetchone()
    planned=int(run['planned'] or 0) if run else 0
    valid_n=int(run['valid'] or valid or 0) if run else int(valid or 0)
    execution_completeness=round(100*valid_n/planned,1) if planned else None

    for x in arr:
        checks=x.get('site_checks') or {}
        category=checks.get('primary_category') or ''
        category_overlap=checks.get('category_exact_overlap_pct')
        site_rel=checks.get('service_relevance_score')
        core=sum(bool(x.get(k)) for k in ('website','address','phone'))

        # RELEVANCE: Google documents the pillar; category is a directly documented
        # relevance signal. Website content is supporting public evidence, not a
        # claimed Google factor weight.
        rel_evidence=[]
        rel_available=0
        rel_total=3
        if category:
            rel_available+=1
            if category_overlap is not None:
                rel_evidence.append(f'Google Maps primary category captured as “{category}”; exact service-term overlap is {category_overlap:.0f}%.')
            else:
                rel_evidence.append(f'Google Maps primary category captured as “{category}”.')
        else:
            rel_evidence.append('Primary category could not be captured reliably; no category claim is made.')
        if site_rel is not None:
            rel_available+=1
            rel_evidence.append(f'Official website service-term relevance measured {site_rel:.0f}/100. This is supporting evidence only, not a published Google weight.')
        elif x.get('website'):
            rel_evidence.append('Official website was verified, but a service-relevance score was not available for this run.')
        else:
            rel_evidence.append('No official website was verified, so website relevance is unavailable.')
        if core:
            rel_available+=1
            rel_evidence.append(f'{core}/3 core public profile/contact fields were captured (website, address, phone).')

        if category and category_overlap is not None and category_overlap >= 75:
            rel_status='Strong documented relevance evidence'
        elif category:
            rel_status='Documented category evidence available'
        else:
            rel_status='Relevance only partially observable'

        # DISTANCE: exact tested coordinates vs captured business coordinates.
        dist_evidence=[]
        dist_available=0
        dist_total=2
        prox=x.get('proximity')
        if x.get('business_coords'):
            dist_available+=1
            dist_evidence.append('Business coordinates were derived from the captured Google Maps listing, allowing point-to-business distance analysis.')
        else:
            dist_evidence.append('Business coordinates could not be derived reliably, so exact distance attribution is withheld.')
        if prox:
            dist_available+=1
            dist_evidence.append(
                f"Top-5 coverage was {prox['near_top5']}% in the nearer half of test searches versus {prox['far_top5']}% in the farther half; "
                f"the measured gap was {prox['delta']} percentage points."
            )
            if prox['delta'] >= 20:
                dist_status='Strong proximity pattern observed'
            elif abs(prox['delta']) <= 10:
                dist_status='Weak proximity dependence observed'
            else:
                dist_status='Mixed proximity pattern observed'
        else:
            dist_status='Distance effect not fully measurable'

        # PROMINENCE: Google explicitly names review count/rating and broader web
        # information such as links. We measure reviews directly, but do not invent
        # backlink/offline-prominence data that this system does not possess.
        prom_evidence=[]
        prom_available=0
        prom_total=3
        reviews=x.get('reviews'); rating=x.get('rating')
        if reviews is not None:
            prom_available+=1
            mr=x.get('market_median_reviews')
            rp=x.get('review_percentile')
            if mr is not None:
                prom_evidence.append(f'{reviews:,} Google reviews versus a market median of {mr:,.0f}; review-volume percentile is approximately {rp:.0f}th.')
            else:
                prom_evidence.append(f'{reviews:,} Google reviews captured.')
        else:
            prom_evidence.append('Google review count was not captured reliably.')
        if rating is not None:
            prom_available+=1
            pr=x.get('rating_percentile')
            prom_evidence.append(f'Google rating captured at {rating:.1f}★' + (f', approximately {pr:.0f}th percentile in this scan.' if pr is not None else '.'))
        else:
            prom_evidence.append('Google rating was not captured reliably.')
        prom_evidence.append('Broader web/offline prominence and complete link authority are not measured by this scanner; no value is fabricated for them.')

        rp=x.get('review_percentile')
        if reviews is not None and rating is not None and rp is not None and rp >= 75 and rating >= 4.3:
            prom_status='Strong review-based prominence evidence'
        elif reviews is not None and x.get('market_median_reviews') is not None and reviews < x['market_median_reviews']*.75:
            prom_status='Review prominence is not a leading advantage'
        else:
            prom_status='Mixed/partial prominence evidence'

        # PAID is a separate surface, never folded into organic ranks.
        ad_hits=int(x.get('ad_hits') or 0)
        paid_status='Sponsored placement observed' if ad_hits else 'No Sponsored label observed in this audit'
        paid_evidence=(
            f'Sponsored/Ad labeling was observed in {ad_hits} of {valid_n} valid searches. Organic rank metrics exclude those paid cards.'
            if ad_hits else
            f'No Sponsored/Ad label was captured for this business in {valid_n} valid searches. This does not prove the business never advertises.'
        )

        available=rel_available+dist_available+prom_available+1
        total=rel_total+dist_total+prom_total+1
        factor_coverage=round(100*available/total,1)

        supported=[]; contradicted=[]
        if rel_status.startswith('Strong'): supported.append('strong query/category relevance evidence')
        if dist_status.startswith('Strong'): supported.append('a measurable proximity advantage')
        elif dist_status.startswith('Weak') and x.get('top5',0)>=50: supported.append('visibility that remains strong beyond the closest search points')
        if prom_status.startswith('Strong'): supported.append('strong review-based prominence')
        elif prom_status.startswith('Review prominence is not'): contradicted.append('reviews are below the market benchmark, so review volume does not explain the lead')
        if x.get('head_to_head') and (x['head_to_head'].get('win_rate') or 0) >= 65:
            supported.append('strong head-to-head performance against the leading rival')
        if x.get('majority_top3_points',0) >= max(1,round((x.get('valid_points') or 1)*.6)):
            supported.append('broad geographic ranking consistency')
        if not supported:
            supported.append('the observed ranking pattern itself; public signals do not isolate a single dominant cause')

        x['google_framework']={
            'relevance':{'status':rel_status,'evidence':rel_evidence,'coverage':round(100*rel_available/rel_total,1)},
            'distance':{'status':dist_status,'evidence':dist_evidence,'coverage':round(100*dist_available/dist_total,1)},
            'prominence':{'status':prom_status,'evidence':prom_evidence,'coverage':round(100*prom_available/prom_total,1)},
            'paid':{'status':paid_status,'evidence':[paid_evidence],'coverage':100.0},
            'factor_coverage':factor_coverage,
            'factor_coverage_label':_coverage_label(factor_coverage),
            'execution_completeness':execution_completeness,
            'supported':supported,
            'contradicted':contradicted,
            'valid_searches':valid_n,
            'planned_searches':planned,
        }
    return arr


def _factor_block(title,doc_label,data):
    ev=''.join(f'<li>{html.escape(str(v))}</li>' for v in data.get('evidence',[]))
    return (
        "<div style='margin:12px 0;padding:13px 14px;border:1px solid #e2e8f0;border-radius:11px;background:#fff'>"
        f"<div style='font-weight:900'>{html.escape(title)} <span style='font-size:11px;color:#047857'>• {html.escape(doc_label)}</span></div>"
        f"<div style='margin:5px 0 7px'><b>Assessment:</b> {html.escape(data.get('status',''))} &nbsp; <span class='note'>Data coverage {data.get('coverage',0):.0f}%</span></div>"
        f"<ul style='margin:0;padding-left:20px'>{ev}</ul></div>"
    )


def explanation_html(x,arr,valid):
    f=x.get('google_framework') or {}
    valid_n=f.get('valid_searches') or x.get('valid_searches') or valid or 1
    planned=f.get('planned_searches') or valid_n

    observed=(
        f"<div class='bottomline'><b>Observed ranking result:</b> #{x.get('visibility_rank','?')} of {x.get('market_businesses',len(arr))} businesses in this audit; "
        f"#1 in {x.get('rank1_count',0)}/{valid_n} valid searches, Top 3 in {x.get('top3_count',0)}/{valid_n}, "
        f"Top 5 in {x.get('top5_count',0)}/{valid_n}, median shown organic rank #{x.get('median')}. "
        f"It was majority-Top-3 at {x.get('majority_top3_points',0)}/{x.get('valid_points',0)} valid grid points.</div>"
    )

    blocks=''
    if f:
        blocks+=_factor_block('1. Relevance','Google-documented local-ranking pillar',f.get('relevance',{}))
        blocks+=_factor_block('2. Distance','Google-documented local-ranking pillar',f.get('distance',{}))
        blocks+=_factor_block('3. Prominence','Google-documented local-ranking pillar',f.get('prominence',{}))
        blocks+=_factor_block('4. Paid visibility','Separate from organic ranking',f.get('paid',{}))

    h=x.get('head_to_head')
    h2h=''
    if h and h.get('comparable'):
        h2h=(f"<div style='margin:10px 0'><b>Head-to-head:</b> against {html.escape(h.get('rival_name','leading rival'))}, "
             f"this business ranked above it in {h.get('wins',0)}/{h.get('comparable',0)} comparable searches ({h.get('win_rate',0)}%).</div>")

    supported=f.get('supported') or []
    contradicted=f.get('contradicted') or []
    conclusion=("<div class='bottomline'><b>Evidence-backed conclusion:</b> "+html.escape('; '.join(supported))+".</div>")
    if contradicted:
        conclusion+=("<div class='notwhy'><b>Evidence that argues against a simple explanation:</b><ul>"+
                     ''.join(f'<li>{html.escape(v)}</li>' for v in contradicted)+"</ul></div>")

    completeness=f.get('execution_completeness')
    trace=(
        "<div style='margin-top:12px;padding-top:10px;border-top:1px solid #e2e8f0'>"
        f"<b>Traceability:</b> {valid_n}/{planned} planned searches were valid"
        +(f" ({completeness:.1f}% execution completeness)." if completeness is not None else '.')+
        f" Public-factor data coverage for this business: <b>{f.get('factor_coverage_label','Limited')} ({f.get('factor_coverage',0):.0f}%)</b>."
        "<br><span class='note'>This coverage score measures how much public evidence was captured; it is not a Google ranking score or probability.</span></div>"
    )

    disclaimer=(
        "<p class='note'><b>Accuracy boundary:</b> Every numeric statement above is computed from captured scan/profile data. "
        "Google publicly identifies relevance, distance and prominence as the main local-ranking pillars but keeps the detailed algorithm and factor weights confidential. "
        "Therefore this system can provide exact observed evidence and a defensible explanation, but no outside tool can truthfully provide 100% causal certainty for why Google chose a specific rank.</p>"
    )

    return ("<details class='why'><summary>Why is this business actually on top?</summary><div class='whybox'>"+
            observed+h2h+blocks+conclusion+trace+disclaimer+"</div></details>")


def patch():
    enh.aggregate=aggregate
    enh.explanation_html=explanation_html
