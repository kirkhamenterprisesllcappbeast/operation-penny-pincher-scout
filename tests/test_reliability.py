"""Offline reliability scenarios; all data and timestamps are synthetic."""
import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'prototype'))
from marketplace_scout import Transport, BASES, discover, run
from public_work import SEARCH, ALGORA, discover_public_work, markdown_report, next_action
from scout_common import FetchError, http_failure

AT = datetime(2026, 1, 1, tzinfo=timezone.utc)
RESET = str(int(AT.timestamp()) + 120)
SECRET = 'SYNTHETIC_SECRET_DO_NOT_REPORT'


def failure(status, headers=None):
    headers = headers or {}
    return http_failure(status, lambda key: headers.get(key), github=True, observed_at=AT)


def search_payload():
    return {'items':[{'id':123, 'html_url':'https://github.com/example/fixture/issues/1',
                      'title':'Bounty $1 USD: synthetic README test', 'body':'Synthetic only',
                      'state':'open','assignees':[]}],
            'total_count':1,'incomplete_results':False}


def based_payload():
    return {'tasks':[{'task_id':'SYNTHETIC_ONLY', 'title':'Synthetic research',
                      'description':'Synthetic only', 'status':'open',
                      'bounty_amount':'1000000', 'bounty_token':'USDC',
                      'escrow':{'status':'funded'}, 'claimable':True}]}


class DiagnosticsTests(unittest.TestCase):
    def test_primary_403_reset(self):
        error = failure(403, {'X-RateLimit-Remaining':'0', 'X-RateLimit-Reset':RESET})
        self.assertEqual(error.code, 'RATE_LIMITED')
        self.assertEqual(error.diagnostics['http_status'], 403)
        self.assertEqual(error.diagnostics['rate_limit_kind'], 'PRIMARY')
        self.assertEqual(error.diagnostics['earliest_retry_at'], '2026-01-01T00:02:00+00:00')

    def test_429_retry_after(self):
        error = failure(429, {'Retry-After':'60'})
        self.assertEqual(error.code, 'RATE_LIMITED')
        self.assertEqual(error.diagnostics['earliest_retry_at'], '2026-01-01T00:01:00+00:00')
        self.assertEqual(error.diagnostics['rate_limit_kind'], 'UNDETERMINED')

    def test_later_of_retry_and_reset(self):
        error = failure(429, {'Retry-After':'60','X-RateLimit-Reset':RESET})
        self.assertEqual(error.diagnostics['earliest_retry_at'], '2026-01-01T00:02:00+00:00')

    def test_retry_after_http_date(self):
        error = failure(403, {'Retry-After':'Thu, 01 Jan 2026 00:03:00 GMT'})
        self.assertEqual(error.code, 'RATE_LIMITED')
        self.assertEqual(error.diagnostics['earliest_retry_at'], '2026-01-01T00:03:00+00:00')
        self.assertEqual(error.diagnostics['rate_limit_kind'], 'UNDETERMINED')

    def test_ordinary_403_not_inferred_from_reset_alone(self):
        for headers in ({}, {'X-RateLimit-Remaining':'10','X-RateLimit-Reset':RESET},
                        {'Retry-After':SECRET}):
            with self.subTest(headers=headers):
                error = failure(403, headers)
                self.assertEqual(error.code, 'ACCESS_DENIED')
                self.assertIsNone(error.diagnostics['earliest_retry_at'])

    def test_missing_and_invalid_headers_have_unknown_cooldown(self):
        for headers in ({}, {'Retry-After':'-1','X-RateLimit-Reset':'NaN'},
                        {'Retry-After':'9'*100,'X-RateLimit-Reset':'9'*100},
                        {'Retry-After':SECRET,'X-RateLimit-Reset':SECRET}):
            with self.subTest(headers=headers):
                error = failure(429, headers)
                self.assertEqual(error.code, 'RATE_LIMITED')
                self.assertEqual(error.diagnostics['retry_time_status'], 'UNKNOWN')
                self.assertIsNone(error.diagnostics['earliest_retry_at'])
                self.assertNotIn(SECRET, json.dumps(error.diagnostics))

    def test_past_reset_does_not_schedule_in_past(self):
        error = failure(429, {'X-RateLimit-Reset':'1'})
        self.assertEqual(error.diagnostics['earliest_retry_at'], AT.isoformat())

    def test_invalid_date_overflow_never_becomes_a_cooldown(self):
        for value in ('Fri, 31 Dec 9999 23:59:59 -1200',
                      'Thu, 01 Jan 2026 00:00:00', SECRET):
            error = failure(429, {'Retry-After':value})
            self.assertEqual(error.diagnostics['retry_after_state'], 'INVALID')
            self.assertIsNone(error.diagnostics['retry_after_at'])
            self.assertIsNone(error.diagnostics['earliest_retry_at'])

    def test_remaining_header_states(self):
        for value, expected in ((None,'ABSENT'), (SECRET,'INVALID'), ('0','VALID')):
            error=failure(429, {'X-RateLimit-Remaining':value})
            self.assertEqual(error.diagnostics['remaining_state'], expected)
            self.assertNotIn(SECRET, json.dumps(error.diagnostics))

    def test_no_retry_and_no_error_body_or_private_header_reads(self):
        for status in (403,429,406,401,402,302):
            with self.subTest(status=status):
                response = Mock(status=status)
                response.reason = SECRET
                response.getheader.side_effect = lambda name, default=None: {
                    'Retry-After':SECRET, 'X-RateLimit-Reset':SECRET,
                    'X-RateLimit-Remaining':'0' if status==403 else None,
                    'Authorization':SECRET, 'Set-Cookie':SECRET}.get(name,default)
                connection = Mock()
                connection.getresponse.return_value = response
                with patch('marketplace_scout.public_dns', return_value=['8.8.8.8']), \
                     patch('marketplace_scout.PinnedHTTPSConnection', return_value=connection):
                    with self.assertRaises(FetchError) as caught:
                        Transport().get(SEARCH+'?q=synthetic')
                self.assertEqual(connection.request.call_count,1)
                connection.close.assert_called_once()
                response.read.assert_not_called()
                self.assertNotIn(SECRET, json.dumps(caught.exception.diagnostics))
                self.assertTrue({call.args[0] for call in response.getheader.call_args_list}
                                <= {'Retry-After','X-RateLimit-Reset','X-RateLimit-Remaining'})

    def test_github_seven_second_spacing(self):
        connection = Mock()
        response = Mock(status=200)
        response.getheader.return_value = 'application/json'
        response.read.return_value = b'{}'
        connection.getresponse.return_value = response
        with patch('marketplace_scout.public_dns', return_value=['8.8.8.8']), \
             patch('marketplace_scout.PinnedHTTPSConnection', return_value=connection), \
             patch('marketplace_scout.time.monotonic', side_effect=[100,100,102,102]), \
             patch('marketplace_scout.time.sleep') as sleep:
            transport = Transport()
            transport.get(SEARCH); transport.get(SEARCH)
        sleep.assert_called_once_with(5)

    def test_credentials_rejected_before_dns_or_connection(self):
        with patch('marketplace_scout.public_dns') as dns, \
             patch('marketplace_scout.PinnedHTTPSConnection') as connect:
            with self.assertRaises(FetchError): Transport().get(SEARCH, token=SECRET)
        dns.assert_not_called(); connect.assert_not_called()

    def test_dns_rejection_is_transport_failure_not_malformed_response(self):
        with patch('marketplace_scout.public_dns', side_effect=ValueError(SECRET)), \
             patch('marketplace_scout.PinnedHTTPSConnection') as connection:
            provider=discover('basedagents', Transport())
        self.assertEqual(provider['status'], 'DNS_VALIDATION_FAILED')
        self.assertNotIn(SECRET, json.dumps(provider))
        connection.assert_not_called()


class DiscoveryReportingTests(unittest.TestCase):
    def test_search_partial_results_survive_later_rate_limit(self):
        error = failure(429, {'Retry-After':'60'})
        transport = Mock(get=Mock(side_effect=[search_payload(),error,FetchError('HTTP_406')]))
        providers = discover_public_work(transport)
        github = providers[0]
        self.assertEqual(len(github['tasks']),1)
        self.assertEqual(github['status'],'RATE_LIMITED')
        self.assertEqual(github['operations_completed'],1)
        self.assertEqual(github['operations_attempted'],2)
        self.assertEqual(github['operations_planned'],3)
        self.assertTrue(github['coverage_interrupted'])
        self.assertEqual(transport.get.call_count,3)  # Two searches, then unrelated Algora.
        self.assertEqual(transport.get.call_args.args[0], ALGORA)
        self.assertEqual(github['errors'][0]['operation'],'search_issues')

    def test_marketplace_partial_results_survive_later_failure(self):
        row={'_id':'SYNTHETIC_ONLY','title':'Synthetic','description':'Synthetic only',
             'status':'open','flow':'claim','payout_cents':1,'currency':'usd'}
        transport=Mock(get=Mock(side_effect=[{'bounties':[row],'has_more':True,'next_cursor':'next'},
                                            failure(429,{'Retry-After':'60'})]))
        provider=discover('bounty',transport)
        self.assertEqual(len(provider['tasks']),1)
        self.assertEqual(provider['status'],'RATE_LIMITED')
        self.assertEqual(provider['operations_completed'],1)
        self.assertEqual(provider['operations_attempted'],2)
        self.assertEqual(transport.get.call_count,2)

    def test_partial_search_survives_network_and_malformed_failures(self):
        for outcome, status in ((OSError(SECRET),'NETWORK_ERROR'), ({},'MALFORMED_RESPONSE')):
            transport=Mock(get=Mock(side_effect=[search_payload(),outcome,failure(406)]))
            provider=discover_public_work(transport)[0]
            self.assertEqual(provider['status'],status)
            self.assertEqual(len(provider['tasks']),1)
            self.assertEqual(provider['operations_completed'],1)
            self.assertNotIn(SECRET,json.dumps(provider))

    def mixed_report(self, github_error=None):
        transport=Mock(get=Mock(side_effect=[failure(401),based_payload(),
                         github_error or failure(429),failure(406)]))
        return run(transport)

    def test_mixed_sources_keep_success_and_diagnostics(self):
        report=self.mixed_report()
        self.assertEqual([p['status'] for p in report['providers']],
                         ['AUTH_REQUIRED','FETCHED','RATE_LIMITED','HTTP_406'])
        self.assertEqual(report['advertised_task_count'],1)
        self.assertEqual(report['qualified_opportunity_count'],0)
        self.assertEqual(report['execution_health'],'REPORT_GENERATED')
        self.assertEqual(report['discovery_coverage'],'PARTIAL')
        self.assertEqual(report['verified_received_usd'],'0.00')
        self.assertFalse(report['financial_milestone'])
        text=markdown_report(report)
        self.assertIn('Discovery coverage: PARTIAL',text)
        self.assertIn('Final workflow result: not observed',text)
        self.assertIn('HTTP 406 response observed; cause unverified',text)
        self.assertIn('Access unavailable under the current credential-free policy',text)
        self.assertIn('inconclusive',text)
        self.assertNotIn('review the original',next_action(report).lower())
        self.assertIn('cooldown unknown',next_action(report))

    def test_known_cooldown_in_empty_queue_action_and_summary(self):
        report=self.mixed_report(failure(429,{'Retry-After':'60'}))
        self.assertIn('do not retry before 2026-01-01T00:01:00+00:00',next_action(report))
        self.assertIn('earliest retry 2026-01-01T00:01:00+00:00',markdown_report(report))

    def test_empty_queue_without_rate_limit(self):
        report=self.mixed_report(failure(403))
        action=next_action(report)
        self.assertIn('No reviewable leads',action)
        self.assertIn('Investigate',action)
        self.assertNotIn('review the original',action.lower())

    def test_complete_zero_results(self):
        # Algora currently has no validated contract; simulate completed providers
        # to challenge reporting without claiming real complete live coverage.
        providers=[{'provider':name,'status':'FETCHED','tasks':[]} for name in BASES]
        with patch('marketplace_scout.discover',side_effect=providers), \
             patch('public_work.discover_public_work',return_value=[]):
            report=run(Mock())
        self.assertEqual(report['discovery_coverage'],'COMPLETE')
        self.assertEqual(report['coverage'],'QUERIED_ALL_SOURCES')
        self.assertEqual(report['qualified_opportunity_count'],0)
        self.assertIn('Discovery coverage: COMPLETE',markdown_report(report))
        self.assertIn('completed run',next_action(report))
        self.assertNotIn('inconclusive',markdown_report(report))

    def test_network_exception_never_leaks_text(self):
        report=run(Mock(get=Mock(side_effect=OSError(SECRET))))
        self.assertTrue(all(p['status']=='NETWORK_ERROR' for p in report['providers']))
        self.assertNotIn(SECRET,json.dumps(report))
        self.assertNotIn(SECRET,markdown_report(report))

    def test_bounded_sample_stays_partial(self):
        payload=search_payload(); payload['total_count']=100
        transport=Mock(get=Mock(side_effect=[payload,payload,payload,failure(406)]))
        provider=discover_public_work(transport)[0]
        self.assertEqual(provider['status'],'PARTIAL_SEARCH_SAMPLE')
        self.assertEqual(provider['coverage_status'],'PARTIAL')
        self.assertEqual(provider['operations_completed'],3)
        self.assertEqual(len(provider['tasks']),1)


if __name__ == '__main__':
    unittest.main()
