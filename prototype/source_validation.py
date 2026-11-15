"""Claim-specific provenance assessment; separate human review input required."""
from datetime import datetime, timezone
from urllib.parse import urlsplit

def assess(claims, reviews):
    results=[]
    for claim in claims:
        rows=[]
        for r in reviews:
            try:
                accessed=datetime.fromisoformat(r['retrieved_at'].replace('Z','+00:00'))
                age=(datetime.now(timezone.utc)-accessed).total_seconds()
                url=urlsplit(r['url']);origin=urlsplit(r['origin'])
                if (r['claim']==claim and r['credible'] is True and r['support_confirmed'] is True
                    and r['reviewer_role']=='human' and r['credibility_basis'].strip()
                    and 0<=age<=90*86400 and url.scheme=='https' and origin.scheme=='https'
                    and url.hostname and origin.hostname and r['publisher'].strip()):
                    rows.append({url.hostname.lower().removeprefix('www.'),origin.geturl(),r['publisher'].lower()})
            except (KeyError,ValueError,TypeError,AttributeError): continue
        # Connected components collapse transitive syndication and common ownership.
        groups=[]
        for row in rows:
            hits=[g for g in groups if g & row]
            merged=set(row)
            for g in hits: merged |= g; groups.remove(g)
            groups.append(merged)
        results.append({'claim':claim,'independent_sources':len(groups)})
    return {'status':'CORROBORATED' if results and all(r['independent_sources']>=3 for r in results) else 'PRELIMINARY',
            'claims':results,'note':'Corroboration does not prove payment or authorize action.'}
