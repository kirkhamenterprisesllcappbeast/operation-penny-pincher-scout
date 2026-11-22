"""Read-only, zero-budget marketplace scout. No write or payment endpoints."""
import argparse
import hashlib
import http.client
import json
import re
import ssl
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlsplit, urlencode
from network_safety import public_dns, PinnedHTTPSConnection
from task_costs import assess_costs, net_return_gate
from scout_common import FetchError, now, http_failure, error_record, coverage_status

BASES = {'bounty': 'https://api.trybounty.ai/v1/agent/bounties',
         'basedagents': 'https://api.basedagents.ai/v1/tasks'}
MAX_BYTES = 2_000_000
PUBLIC_ENDPOINTS = {'https://api.github.com/search/issues', 'https://algora.io/api/bounty.list'}

def validate_destination(url):
    p=urlsplit(url)
    if (p.scheme != 'https' or p.username or p.password or p.port not in (None,443)
            or p.fragment or url.split('?')[0] not in set(BASES.values()) | PUBLIC_ENDPOINTS):
        raise FetchError('UNSAFE_DESTINATION')
    return p

class Transport:
    """Pinned public DNS; exact API paths; no redirects, proxies, writes or retries."""
    security_mode='DIRECT_PINNED_PUBLIC_DNS'
    def __init__(self):
        self.last = {}

    def get(self, url, token=None):
        p = validate_destination(url)
        if token:
            raise FetchError('CREDENTIALS_NOT_SUPPORTED_IN_PUBLIC_RUNNER')
        interval = 7 if p.hostname == 'api.github.com' else 1
        delay = interval - (time.monotonic() - self.last.get(p.hostname, 0))
        if delay > 0:
            time.sleep(delay)
        try:
            pinned_ip = public_dns(p.hostname)[0]
        except ValueError:
            raise FetchError('DNS_VALIDATION_FAILED') from None
        conn = PinnedHTTPSConnection(p.hostname, pinned_ip, 10)
        headers = {'User-Agent':'OperationPennyPincherScout/1.0', 'Accept':'application/json',
                   'X-BasedAgents-Skill-Version':'1.3.9'}
        if p.hostname == 'api.github.com':
            headers['X-GitHub-Api-Version'] = '2022-11-28'
        try:
            conn.request('GET', p.path + ('?' + p.query if p.query else ''), headers=headers)
            r = conn.getresponse()
            if r.status != 200:
                raise http_failure(r.status, r.getheader, github=p.hostname == 'api.github.com')
            if r.getheader('Content-Type','').split(';')[0].strip() != 'application/json':
                raise FetchError('INVALID_CONTENT_TYPE')
            body = r.read(MAX_BYTES+1)
            if len(body) > MAX_BYTES:
                raise FetchError('RESPONSE_TOO_LARGE')
            return json.loads(body)
        finally:
            self.last[p.hostname] = time.monotonic()
            conn.close()

def timestamp(value, milliseconds=False):
    if value is None:
        return None
    if milliseconds:
        if type(value) not in (int,float):
            raise ValueError('Invalid deadline')
        return datetime.fromtimestamp(value/1000,timezone.utc)
    if not isinstance(value,str):
        raise ValueError('Invalid deadline')
    d=datetime.fromisoformat(value.replace('Z','+00:00'))
    if d.tzinfo is None:
        raise ValueError('Missing timezone')
    return d

def normalize(provider, row):
    if not isinstance(row,dict):
        raise ValueError('Task must be object')
    bounty = provider == 'bounty'
    ident = row.get('_id' if bounty else 'task_id')
    if not isinstance(ident,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,200}',ident):
        raise ValueError('Invalid task id')
    for field in ('title','description','status'):
        if not isinstance(row.get(field),str) or not row[field].strip():
            raise ValueError('Missing task text')
    deadline=timestamp(row.get('expires_at'),bounty)
    amount=row.get('payout_cents') if bounty else row.get('bounty_amount')
    if amount is not None:
        if isinstance(amount,bool) or not re.fullmatch(r'\d{1,24}',str(amount)):
            raise ValueError('Invalid atomic reward')
        reward=Decimal(str(amount))/Decimal(100 if bounty else 1000000)
    else:
        reward=None
    capabilities=row.get('required_capabilities') or []
    if not isinstance(capabilities,list) or any(not isinstance(x,str) for x in capabilities):
        raise ValueError('Invalid capabilities')
    reasons=[]
    if row['status'] != 'open': reasons.append('NOT_OPEN')
    if deadline and deadline <= datetime.now(timezone.utc): reasons.append('EXPIRED')
    if row.get('test') is True: reasons.append('TEST_TASK_NO_MONEY')
    if reward is None or reward <= 0: reasons.append('NO_FIXED_PAID_REWARD')
    funded = (row.get('escrow') or {}).get('status') == 'funded' if not bounty else row.get('flow') == 'claim'
    if not funded: reasons.append('FUNDING_NOT_ESTABLISHED')
    if not bounty:
        if row.get('claimable') is not True: reasons.append('NOT_CLAIMABLE')
        reasons.append('UPFRONT_1_USDC_CLAIM_BOND')
    text=(row['title']+' '+row['description']).lower()
    for phrase in ('no ai','no automation','human only','seed phrase','private key','buy tokens','deposit money','pay to apply','fake review','captcha'):
        if phrase in text: reasons.append('UNSUITABLE_OR_MANUAL_REVIEW:'+phrase)
    missing=sorted(set(capabilities)-{'research','code','content','data','analysis','data-processing','documentation'})
    if missing: reasons.append('CAPABILITY_REVIEW_REQUIRED')
    cost_assessment = assess_costs(row['description'], reward)
    reasons.extend(cost_assessment['reasons'])
    # Platform listing alone cannot establish zero expense, eligibility or legitimacy.
    reasons.extend(['ELIGIBILITY_REVIEW_REQUIRED','EXPENSES_UNKNOWN','PRELIMINARY_THREE_SOURCE_REVIEW'])
    return {'provider':provider,'task_id':ident,'source_url':BASES[provider]+'/'+ident,
            'retrieved_at':now(),'title':row['title'],'description':row['description'],
            'requirements':row.get('verification') if bounty else row.get('expected_output'),
            'required_capabilities':capabilities,'missing_capabilities':missing,
            'reward':str(reward) if reward is not None else None,
            'currency':row.get('currency') if bounty else row.get('bounty_token'),
            'network':row.get('bounty_network'),'deadline':deadline.isoformat() if deadline else None,
            'version':row.get('version'),'funding_status':'PLATFORM_ADVERTISES_FUNDED' if funded else 'UNKNOWN',
            'ai_permission':'PLATFORM_SUPPORTS_AGENTS_TASK_REVIEW_PENDING',
            'authentication':'Agent bearer key; completed Agent Card; Stripe Connect identity and payout onboarding' if bounty else 'Registered Ed25519 agent; signed delivery; verified payout wallet; bonded slot',
            'upfront_cost': 'UNKNOWN' if bounty else '1 USDC refundable bond at risk; fees UNKNOWN',
            'cost_assessment': cost_assessment,
            'net_return_assessment': net_return_gate(None),
            'effort_hours':None,'expected_payout':None,'acceptance_probability':None,
            'estimate_note':'UNKNOWN: no calibrated model or task-specific execution assessment',
            'state':'VALIDATING','verification_status':'PRELIMINARY','eligible':False,'reasons':reasons,
            'raw_sha256':hashlib.sha256(json.dumps(row,sort_keys=True).encode()).hexdigest()}

def discover(provider, transport):
    tasks=[]; seen=set(); cursor=None; offset=0; page_count=0; errors=[]; completed=0
    try:
        for page_count in range(1,6):
            query={'status':'open','limit':100}
            if provider=='basedagents': query.update(offset=offset,min_usdc='0.000001')
            elif cursor: query['cursor']=cursor
            url=BASES[provider]+'?'+urlencode(query)
            payload=transport.get(url)
            key='bounties' if provider=='bounty' else 'tasks'
            if not isinstance(payload,dict) or not isinstance(payload.get(key),list):
                raise ValueError('Invalid list envelope')
            rows=payload[key]
            completed += 1
            for row in rows:
                try:
                    task=normalize(provider,row)
                    if task['task_id'] in seen: continue
                    seen.add(task['task_id']);tasks.append(task)
                except (ValueError,TypeError,AttributeError,OverflowError): errors.append('MALFORMED_TASK')
            if provider=='bounty':
                if type(payload.get('has_more')) is not bool: raise ValueError('Missing pagination')
                if not payload['has_more']: break
                next_cursor=payload.get('next_cursor')
                if not isinstance(next_cursor,str) or not next_cursor or next_cursor==cursor: raise ValueError('Invalid cursor')
                cursor=next_cursor
            else:
                if len(rows)<100: break
                offset+=100
        else:
            return {'provider':provider,'status':'PARTIAL_PAGE_LIMIT','tasks':tasks,'errors':errors,
                    'pages':page_count,'coverage_status':'PARTIAL','coverage_interrupted':True,
                    'operations_attempted':page_count,'operations_completed':completed,
                    'coverage_note':'Stopped at the five-page bound; more pages may exist.'}
        status='PARTIAL_MALFORMED' if errors else 'FETCHED'
    except FetchError as e:
        status=e.code
        errors.append(error_record(e, provider, 'list_open_tasks'))
    except (ValueError,TypeError,AttributeError):
        status='MALFORMED_RESPONSE'; errors.append({'code':status,'source':provider,'operation':'list_open_tasks'})
    except (OSError,ssl.SSLError,http.client.HTTPException):
        status='NETWORK_ERROR'; errors.append({'code':status,'source':provider,'operation':'list_open_tasks'})
    return {'provider':provider,'status':status,'tasks':tasks,'errors':errors,'pages':page_count,'checked_at':now(),
            'coverage_status':coverage_status(status),'coverage_interrupted':status!='FETCHED',
            'operations_attempted':page_count,'operations_completed':completed}

def run(transport=None):
    from public_work import discover_public_work, rank_leads
    transport=transport or Transport()
    providers=[discover(p,transport) for p in BASES]
    providers.extend(discover_public_work(transport))
    tasks=[t for p in providers for t in p['tasks']]
    viable=[t for t in tasks if t['eligible']]
    return {'generated_at':now(),'mode':'LIVE_READ_ONLY','providers':providers,
            'execution_health':'REPORT_GENERATED','workflow_result':'NOT_OBSERVED_BY_SCOUT',
            'discovery_coverage':'COMPLETE' if all(p['status']=='FETCHED' for p in providers) else 'PARTIAL',
            'advertised_task_count':len(tasks),'qualified_opportunity_count':len(viable),
            'transport_security':getattr(type(transport),'security_mode','CUSTOM_TRANSPORT_NOT_VERIFIED'),
            'runtime_policy':{'paid_subscriptions_allowed':False,'paid_api_calls_allowed':False,
                'automatic_spending_allowed':False,'paid_model_calls':False,
                'third_party_python_packages_required':False,'credentials_supported':False,
                'default_execution':'LOCAL','hosting_costs':'Use an existing computer; cloud-host billing is outside this program'},
            'ranked_viable':sorted(viable,key=lambda t:Decimal(t['reward']),reverse=True),
            'ranked_preliminary':rank_leads(tasks),
            'status':'ZERO VERIFIED OPPORTUNITIES' if not viable else 'APPROVAL_REQUIRED',
            'coverage':'INCOMPLETE' if any(p['status']!='FETCHED' for p in providers) else 'QUERIED_ALL_SOURCES',
            'verified_received_usd':'0.00','financial_milestone':False,
            'warning':'Listing and escrow assertions do not independently prove payment, legitimacy or acceptance.'}

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,default=Path('scout-report.json'))
    parser.add_argument('--markdown',type=Path)
    args=parser.parse_args()
    report=run()
    args.output.write_text(json.dumps(report,indent=2)+'\n')
    if args.markdown:
        from public_work import markdown_report
        args.markdown.write_text(markdown_report(report),encoding='utf-8')
    print(report['status']+'; coverage='+report['coverage']+'; verified receipts=$0.00')
