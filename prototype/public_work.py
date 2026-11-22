"""Public paid-work signals, including rewards below one dollar. No writes.

Issue text is untrusted. We never fetch its links, install its code, obey its
instructions or infer that a sponsor permits AI contributions.
"""
import hashlib
import http.client
import json
import re
from ipaddress import ip_address
from decimal import Decimal
from urllib.parse import urlencode, urlsplit
from scout_common import FetchError, now, error_record, coverage_status
from task_costs import assess_costs, net_return_gate

SEARCH = 'https://api.github.com/search/issues'
QUERIES = ('is:issue is:open label:bounty',
           'is:issue is:open bounty documentation in:title,body',
           'is:issue is:open bounty research in:title,body')
ALGORA = 'https://algora.io/api/bounty.list'


def normalize_issue(row):
    if not isinstance(row, dict) or 'pull_request' in row:
        raise ValueError('Not an issue')
    source = row.get('html_url')
    match = re.fullmatch(r'https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/issues/([1-9]\d*)', source or '')
    if not match or type(row.get('id')) is not int or not isinstance(row.get('title'), str):
        raise ValueError('Invalid public issue')
    body = row.get('body') or ''
    if not isinstance(body, str):
        raise ValueError('Invalid description')
    title = row['title']
    text = (title + '\n' + body).lower()
    reasons = ['AI_POLICY_UNKNOWN','PAYMENT_TERMS_UNVERIFIED','ELIGIBILITY_REVIEW_REQUIRED',
               'EXPENSES_UNKNOWN','PRELIMINARY_THREE_SOURCE_REVIEW']
    rejection = []
    original_source=source
    original_match=re.search(r'(?:###\s*原始链接\s*/\s*Source URL|\*\*Enlace:\*\*)\s*(?:\[)?(https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/issues/[1-9]\d*)',body,re.I)
    if original_match and original_match.group(1)!=source:
        original_source=original_match.group(1)
        rejection.append('MIRRORED_OFFER_REQUIRES_ORIGINAL_VALIDATION')
    if row.get('state') != 'open': rejection.append('NOT_OPEN')
    if row.get('assignees'): rejection.append('ALREADY_ASSIGNED_REVIEW_REQUIRED')
    # Accept only an explicit title amount or bounty command as a reward signal;
    # arbitrary dollar amounts in prose can be costs or someone else's proposal.
    amounts = re.findall(r'\$\s*(\d[\d,]*(?:\.\d{1,6})?)', title)
    excerpts = [title] if amounts else []
    if not amounts:
        for cmd in re.finditer(r'/bounty\s+\$\s*(\d[\d,]*(?:\.\d{1,6})?)', body, re.I):
            amounts.append(cmd.group(1)); excerpts.append(cmd.group(0))
    distinct = sorted({Decimal(a.replace(',','')) for a in amounts})
    reward = str(distinct[0]) if len(distinct) == 1 else None
    if reward is None: reasons.append('REWARD_UNCONFIRMED')
    elif Decimal(reward) <= 0: rejection.append('ZERO_REWARD')
    currency = ('USDC' if re.search(r'\bUSDC\b', title) else
                'USD' if re.search(r'\bUSD\b', title) else 'DOLLAR_UNSPECIFIED') if reward is not None else None
    if currency == 'DOLLAR_UNSPECIFIED': reasons.append('CURRENCY_CONFIRMATION_REQUIRED')
    if re.search(r'\b(?:i propose|i am proposing)\b', text) or 'grant proposal' in title.lower():
        rejection.append('APPLICANT_PROPOSAL_NOT_AVAILABLE_WORK')
    if any(s in text for s in ('reputation points; not money','reputation only','points only','no monetary reward')):
        rejection.append('NONCASH_REWARD')
    if any(s in text for s in ('no ai','no automation','human only','ai-generated contributions are not accepted')):
        rejection.append('AUTOMATION_PROHIBITED_OR_REQUIRES_REVIEW')
    if any(s in text for s in ('bounty alert:', 'new paid opportunity(ies)', 'new opportunityies found')):
        rejection.append('AGGREGATED_ALERT_NOT_ORIGINAL_OFFER')
    if any(s in text for s in ('mirror notice', 'mirrored board thread', 'regression mirror')):
        rejection.append('MIRRORED_OFFER_REQUIRES_ORIGINAL_VALIDATION')
    for link in re.findall(r'https?://[^\s<>`]+', body):
        try:
            host = urlsplit(link).hostname
            if host:
                ip_address(host)
                rejection.append('IP_LITERAL_ENDPOINT_REQUIRES_SECURITY_REVIEW')
                break
        except ValueError:
            pass
    if any(s in text for s in ('seed phrase','private key','deposit to claim','pay to claim','purchase required','back when it clears')):
        rejection.append('SENSITIVE_OR_SPENDING_REQUIREMENTS')
    if any(s in text for s in ('wormhole n150','gpu required','buy hardware')):
        rejection.append('SPECIAL_HARDWARE_NOT_CONFIRMED_AVAILABLE')
    costs = assess_costs(body, reward)
    rejection.extend(costs['reasons'])
    category = 'documentation' if re.search(r'\b(?:documentation|readme|translation|i18n)\b', text) else 'software'
    if category == 'software' and re.search(r'\b(?:research|analysis|data collection)\b', title.lower()):
        category = 'research'
    return {'provider':'github_public_work','task_id':str(row['id']), 'source_url':source,
            'retrieved_at':now(),'source_updated_at':row.get('updated_at'),
            'title':title,'description':body,'requirements':body,'reward':reward,
            'reward_evidence':excerpts,'currency':currency,'category':category,
            'deadline':None,'deadline_status':'UNKNOWN_REQUIRES_OFFER_REVIEW',
            'funding_status':'UNVERIFIED','ai_permission':'UNKNOWN_REPOSITORY_POLICY_REVIEW_REQUIRED',
            'authentication':'Public reads require no account; contribution and payout setup require owner approval',
            'effort_hours':None,'expected_payout':None,'acceptance_probability':None,
            'cost_assessment':costs,'state':'VALIDATING','verification_status':'PRELIMINARY',
            'net_return_assessment':net_return_gate(None),
            'eligible':False,'reasons':reasons+rejection,'rejected':bool(rejection),
            'independent_source_count':1,'original_source_url':original_source,
            'repository':match.group(1)+'/'+match.group(2),
            'raw_sha256':hashlib.sha256(json.dumps(row,sort_keys=True).encode()).hexdigest()}


def discover_public_work(transport):
    tasks=[]; seen=set(); errors=[]; queries=[]; totals=[]; partial=False; status='FETCHED'
    try:
        for query in QUERIES:
            url=SEARCH+'?'+urlencode({'q':query,'sort':'updated','order':'desc','per_page':20})
            queries.append({'query':query,'url':url,'checked_at':now()})
            data=transport.get(url)
            if not isinstance(data,dict) or not isinstance(data.get('items'),list) or type(data.get('total_count')) is not int:
                raise ValueError('Invalid search envelope')
            totals.append(data['total_count'])
            partial |= data.get('incomplete_results') is not False or data['total_count'] > len(data['items'])
            for row in data['items']:
                try:
                    task=normalize_issue(row)
                    if task['source_url'] in seen: continue
                    seen.add(task['source_url']);tasks.append(task)
                except (ValueError,TypeError,AttributeError): errors.append('MALFORMED_ISSUE')
        status='PARTIAL_SEARCH_SAMPLE' if partial else 'FETCHED'
        if errors: status='PARTIAL_MALFORMED'
    except FetchError as e:
        status=e.code; errors.append(error_record(e, 'github_public_work', 'search_issues'))
    except (ValueError,TypeError,AttributeError):
        status='MALFORMED_RESPONSE'; errors.append({'code':status,'source':'github_public_work','operation':'search_issues'})
    except (OSError,http.client.HTTPException):
        status='NETWORK_ERROR'; errors.append({'code':status,'source':'github_public_work','operation':'search_issues'})
    results=[{'provider':'github_public_work','status':status,'tasks':tasks,'errors':errors,
              'queries':queries,'total_matches_per_query':totals,'checked_at':now(),
              'coverage_status':coverage_status(status),
              'coverage_interrupted':len(totals)<len(QUERIES) or status=='PARTIAL_MALFORMED',
              'operations_planned':len(QUERIES),'operations_attempted':len(queries),
              'operations_completed':len(totals),
              'coverage_note':'Bounded sample, not exhaustive; Overlapping searches are not independent corroboration.'}]
    # Probe only the configured route; record the observed outcome without
    # scraping a replacement, assuming a current error code or inventing work.
    algora_errors=[]
    try:
        response=transport.get(ALGORA)
        algora_status='UNSUPPORTED_API_CONTRACT_REVIEW_REQUIRED'
    except FetchError as e:
        algora_status=e.code; algora_errors.append(error_record(e, 'algora', 'list_bounties'))
    except (ValueError,TypeError):
        algora_status='MALFORMED_RESPONSE'; algora_errors.append({'code':algora_status,'source':'algora','operation':'list_bounties'})
    except (OSError,http.client.HTTPException):
        algora_status='NETWORK_ERROR'; algora_errors.append({'code':algora_status,'source':'algora','operation':'list_bounties'})
    results.append({'provider':'algora','status':algora_status,'tasks':[],
                    'errors':algora_errors,'coverage_status':'PARTIAL','coverage_interrupted':True,
                    'operations_attempted':1,'operations_completed':0,
                    'source_url':ALGORA,'checked_at':now(),
                    'note':'No functioning current public listing contract verified. This is not proof of zero available bounties.'})
    return results


def rank_leads(tasks):
    """Review order, not investment advice or expected earnings. Never compare FX."""
    leads=[t for t in tasks if t.get('provider')=='github_public_work' and not t.get('rejected')]
    leads.sort(key=lambda t:(t['reward'] is None,t['category']!='documentation',t['source_url']))
    return [{'review_rank':index+1,'task_id':t['task_id'],'source_url':t['source_url'],
             'title':t['title'],'advertised_reward':t['reward'],'currency':t['currency'],
             'status':'PRELIMINARY_NOT_QUALIFIED','reasons':t['reasons'],
             'ranking_basis':'Explicit advertised amount first, then documentation fit; stable URL tie-break. No inferred payout probability.'}
            for index,t in enumerate(leads)]


def next_action(report):
    limited=[p for p in report['providers'] if p['status']=='RATE_LIMITED']
    if limited:
        actions=[]
        for provider in limited:
            times=[e.get('earliest_retry_at') for e in provider.get('errors',[]) if isinstance(e,dict)]
            times=[t for t in times if t]
            actions.append(provider['provider']+(': do not retry before '+max(times)+
                           ' (header-based lower bound; access is not guaranteed)' if times else
                           ': cooldown unknown; investigate rate-limit evidence before retrying'))
        return '; '.join(actions)+'. No automatic retries.'
    if report.get('ranked_preliminary'):
        return 'Review the original terms and AI policy of one preliminary lead before preparing work.'
    blocked=[p for p in report['providers'] if p['status']!='FETCHED']
    if blocked:
        return ('No reviewable leads. Investigate the source conditions listed below; current coverage is incomplete. '
                'Do not add authentication or bypass access restrictions without authorization.')
    return 'No reviewable leads or qualifying opportunities in this completed run; wait for the next scheduled scan.'


def markdown_report(report):
    lines=['# Scout discovery report', '', report['generated_at'], '',
           report['status']+'. Verified receipts: $'+report['verified_received_usd']+'.', '',
           'Software execution: report generated. Final workflow result: not observed by Scout.',
           'Discovery coverage: '+('COMPLETE' if all(p['status']=='FETCHED' for p in report['providers']) else 'PARTIAL')+'.',
           'Advertised tasks/signals: '+str(sum(len(p['tasks']) for p in report['providers']))+
           '. Reviewable leads: '+str(len(report.get('ranked_preliminary',[])))+
           '. Qualified opportunities: '+str(len(report.get('ranked_viable',[])))+'.', '',
           '| Source | Result | Advertised tasks/signals retrieved |', '|---|---|---:|']
    for provider in report['providers']:
        lines.append('| '+provider['provider']+' | '+provider['status']+' | '+str(len(provider['tasks']))+' |')
    lines.extend(['', 'Source conditions and coverage:'])
    for provider in report['providers']:
        name=provider['provider']; status=provider['status']
        scope=''
        if 'operations_attempted' in provider:
            scope=('; operations completed/attempted: '+str(provider['operations_completed'])+'/'+
                   str(provider['operations_attempted']))
            if 'operations_planned' in provider:
                scope+='; planned: '+str(provider['operations_planned'])
        lines.append('- '+name+': '+coverage_status(status)+scope+'.')
        for error in provider.get('errors',[]):
            if not isinstance(error,dict):
                continue
            # Only internally classified fields; never response bodies or header values.
            detail=error['code']
            if error.get('http_status'):
                detail+='; HTTP '+str(error['http_status'])
            if error.get('rate_limit_kind'):
                detail+='; rate-limit type '+error['rate_limit_kind']
                detail+='; Retry-After '+error['retry_after_state']+'; reset '+error['reset_state']
                if error.get('retry_after_at'):
                    detail+='; Retry-After time '+error['retry_after_at']
                if error.get('rate_limit_reset_at'):
                    detail+='; reset time '+error['rate_limit_reset_at']
            if error['code']=='RATE_LIMITED':
                detail+='; earliest retry '+(error.get('earliest_retry_at') or 'UNKNOWN')
            lines.append('  - '+detail+'.')
        if status=='AUTH_REQUIRED':
            lines.append('  - Access unavailable under the current credential-free policy; this is not an empty marketplace.')
        elif status=='HTTP_406':
            lines.append('  - HTTP 406 response observed; cause unverified. It does not establish permanent unavailability.')
        elif status=='PARTIAL_SEARCH_SAMPLE':
            lines.append('  - Bounded search sample; not an exhaustive search.')
    if any(p['status']!='FETCHED' for p in report['providers']):
        lines.extend(['', 'Incomplete coverage: zero qualified opportunities is inconclusive about available work.'])
    lines.extend(['', 'Review queue — PRELIMINARY, not qualified or recommended paid work.',
                  'Each listing establishes only what its publisher advertises. Reward, AI permission, costs,',
                  'deadline, ownership, eligibility and payment reliability need verification.', '',
                  '| Review order | Issue | Advertised amount | Currency |', '|---:|---|---:|---|'])
    for task in report.get('ranked_preliminary',[])[:10]:
        lines.append('| '+str(task['review_rank'])+' | ['+task['task_id']+']('+task['source_url']+') | '+
                     (task['advertised_reward'] or 'UNKNOWN')+' | '+(task['currency'] or 'UNKNOWN')+' |')
    lines.extend(['', 'Ranking prioritizes an explicit reward signal, then documentation fit; no currency conversion',
                  'or estimated success probability. Full descriptions and rejection reasons are in the JSON report.',
                  'A partial search sample cannot establish that no other opportunities exist.', '',
                  'Next action: '+next_action(report),
                  'No contact, claim, account creation, publication, installation or transaction is authorized by this report.', ''])
    return '\n'.join(lines)
