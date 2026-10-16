"""Offline regression tests; fake responses are not live paper records."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import requests
import yaml
import run
from radar.collect import Result, arxiv, openreview, source_error, source_terms
from radar.core import Store, make_record

ROOT = Path(__file__).resolve().parents[1]
CFG = yaml.safe_load((ROOT / 'config.yaml').read_text(encoding='utf-8'))
VENUE = 'ICLR.cc/2026/Conference'
SINCE = '2026-09-16T00:00:00+00:00'


def config(terms=None):
    c = copy.deepcopy(CFG)
    c['topics'] = [{'id': 'agents', 'label': 'Agents', 'weight': 3,
                    'terms': terms or ['llm agent']}]
    c['fetch']['max_pages'] = 3
    return c


def http_error(status, name='TestError'):
    response = requests.Response()
    response.status_code = status
    response.url = 'https://api2.openreview.net/notes'
    response._content = json.dumps({'name': name, 'token': 'DO_NOT_LOG_THIS'}).encode()
    return requests.HTTPError('upstream refused', response=response)


def note(nid='public', venue=VENUE, **extra):
    return {'id': nid, 'readers': ['everyone'], 'content': {
        'title': {'value': 'Reliable LLM agents'},
        'abstract': {'value': 'llm agent uncertainty'},
        'authors': {'value': ['Fixture Author']},
        'venueid': {'value': venue}}, **extra}


def feed(n=1, total=None, stamp='2026-10-01T00:00:00Z'):
    entries = ''.join(f'''<entry><id>https://arxiv.org/abs/2610.{i:05d}</id>
        <title>Reliable LLM agents {i}</title><published>{stamp}</published>
        <updated>{stamp}</updated><summary>llm agent test</summary>
        <author><name>Fixture Author</name></author></entry>''' for i in range(n))
    return (f'<feed xmlns="http://www.w3.org/2005/Atom" '
            f'xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">'
            f'<opensearch:totalResults>{n if total is None else total}</opensearch:totalResults>'
            f'{entries}</feed>').encode()


class FakeHTTP:
    def __init__(self, callback):
        self.callback = callback
        self.calls = []

    def get(self, url, params=None, **kw):
        self.calls.append((url, params))
        value = self.callback(url, params)
        return SimpleNamespace(content=value, json=lambda: value)


class ArxivHotfixTests(unittest.TestCase):
    def test_all_terms_batched_without_duplicates(self):
        c = config([f'term {n}' for n in range(17)])
        h = FakeHTTP(lambda url, params: feed())
        r = arxiv(h, c, SINCE)
        self.assertEqual(len(h.calls), 3)
        self.assertEqual([p['search_query'].count('all:') for _, p in h.calls], [8, 8, 1])
        self.assertTrue(all(p['max_results'] <= 100 for _, p in h.calls))
        self.assertEqual(len(r.records), 1)
        self.assertTrue(r.complete)
        self.assertFalse(r.failed)

    def test_successful_batch_survives_another_failure(self):
        c = config([f'term {n}' for n in range(9)])
        def callback(url, params):
            if 'all:"term 8"' in params['search_query']:
                raise http_error(500)
            return feed()
        r = arxiv(FakeHTTP(callback), c, SINCE)
        self.assertEqual(len(r.records), 1)
        self.assertFalse(r.complete)
        self.assertTrue(r.failed)

    def test_consistent_outage_stops_after_two_batches(self):
        def callback(url, params):
            raise http_error(500)
        h = FakeHTTP(callback)
        r = arxiv(h, config([f'term {n}' for n in range(40)]), SINCE)
        self.assertEqual(len(h.calls), 2)
        self.assertTrue(r.failed)
        self.assertFalse(r.complete)

    def test_empty_feed_is_valid(self):
        r = arxiv(FakeHTTP(lambda u, p: feed(0)), config(), SINCE)
        self.assertEqual(r.records, [])
        self.assertTrue(r.complete)
        self.assertFalse(r.failed)

    def test_inconsistent_empty_page_is_failure(self):
        r = arxiv(FakeHTTP(lambda u, p: feed(0, total=2)), config(), SINCE)
        self.assertFalse(r.complete)
        self.assertTrue(r.failed)

    def test_pages_continue_to_reported_total(self):
        c = config()
        c['sources']['arxiv']['page_size'] = 1
        h = FakeHTTP(lambda u, p: feed(1, total=2))
        r = arxiv(h, c, SINCE)
        self.assertEqual([p['start'] for _, p in h.calls], [0, 1])
        self.assertTrue(r.complete)

    def test_date_cutoff_stops_pagination(self):
        h = FakeHTTP(lambda u, p: feed(1, total=100, stamp='2026-01-01T00:00:00Z'))
        r = arxiv(h, config(), SINCE)
        self.assertEqual(len(h.calls), 1)
        self.assertEqual(r.records, [])
        self.assertTrue(r.complete)

    def test_page_budget_stays_partial(self):
        c = config()
        c['fetch']['max_pages'] = 1
        r = arxiv(FakeHTTP(lambda u, p: feed(1, total=100)), c, SINCE)
        self.assertFalse(r.complete)
        self.assertFalse(r.failed)
        self.assertEqual(len(r.records), 1)

    def test_terms_not_replaced_or_deleted(self):
        original = list(dict.fromkeys(t for topic in CFG['topics'] for t in topic['terms']))
        self.assertEqual(source_terms(CFG), original)

    def test_error_summary_does_not_leak_payload(self):
        message = source_error(http_error(403, 'ChallengeRequiredError'))
        self.assertIn('HTTP 403', message)
        self.assertIn('ChallengeRequiredError', message)
        self.assertNotIn('DO_NOT_LOG_THIS', message)


class OpenReviewHotfixTests(unittest.TestCase):
    def test_direct_list_success_is_unchanged(self):
        h = FakeHTTP(lambda u, p: {'notes': [note()]})
        r = openreview(h, config(), VENUE)
        self.assertTrue(r.complete)
        self.assertFalse(r.failed)
        self.assertEqual(len(h.calls), 1)
        self.assertEqual(len(r.records), 1)

    def test_search_never_accepts_unaccepted_other_venue_or_private(self):
        def callback(url, params):
            if not url.endswith('/search'):
                raise http_error(403, 'ChallengeRequiredError')
            return {'notes': [note(), note('pending', VENUE + '/Submission'),
                              note('other', 'NeurIPS.cc/2026/Conference'),
                              note('review', replyto='public'),
                              note('private', readers=['~Private_User1'])]}
        h = FakeHTTP(callback)
        r = openreview(h, config(), VENUE)
        self.assertEqual(len(r.records), 1)
        self.assertTrue(r.records[0]['url'].endswith('id=public'))
        self.assertFalse(r.complete)
        self.assertFalse(r.failed)
        self.assertIn('不是完整会集', r.note)
        self.assertEqual(h.calls[-1][1]['group'], VENUE)

    def test_search_failure_keeps_hard_alarm(self):
        def callback(url, params):
            raise http_error(403)
        h = FakeHTTP(callback)
        r = openreview(h, config(), VENUE)
        self.assertTrue(r.failed)
        self.assertFalse(r.complete)
        self.assertEqual(len(h.calls), 2)
        again = openreview(h, config(), 'ICML.cc/2026/Conference')
        self.assertEqual(len(h.calls), 2)
        self.assertTrue(again.failed)

    def test_search_results_deduplicated(self):
        def callback(url, params):
            if not url.endswith('/search'):
                raise http_error(403)
            return {'notes': [note()]}
        r = openreview(FakeHTTP(callback), config(['llm agent', 'uncertainty']), VENUE)
        self.assertEqual(len(r.records), 1)

    def test_search_budget_is_bounded_and_visible(self):
        c = config(['llm agent', 'uncertainty'])
        c['sources']['openreview']['search_max_requests'] = 1
        def callback(url, params):
            if not url.endswith('/search'):
                raise http_error(403)
            return {'notes': [note()]}
        h = FakeHTTP(callback)
        r = openreview(h, c, VENUE)
        self.assertEqual(len(h.calls), 2)
        self.assertFalse(r.complete)
        self.assertIn('1/2', r.note)
        self.assertIn('预算', r.note)

    def test_mid_search_failure_keeps_previous_records(self):
        def callback(url, params):
            if not url.endswith('/search'):
                raise http_error(403)
            if params['term'] == 'uncertainty':
                raise http_error(503)
            return {'notes': [note()]}
        r = openreview(FakeHTTP(callback), config(['llm agent', 'uncertainty']), VENUE)
        self.assertEqual(len(r.records), 1)
        self.assertTrue(r.failed)

    def test_search_deny_is_not_hidden_as_empty_success(self):
        def callback(url, params):
            if not url.endswith('/search'):
                raise http_error(403)
            return {'error': 'not authorized'}
        r = openreview(FakeHTTP(callback), config(), VENUE)
        self.assertTrue(r.failed)

    def test_full_list_second_page_failure_keeps_previous_records(self):
        c = config()
        c['sources']['openreview']['page_size'] = 1
        def callback(url, params):
            if params['offset']:
                raise http_error(503)
            return {'notes': [note()]}
        r = openreview(FakeHTTP(callback), c, VENUE)
        self.assertEqual(len(r.records), 1)
        self.assertTrue(r.failed)

    def test_fallback_can_be_disabled_without_silencing_error(self):
        c = config()
        c['sources']['openreview']['search_fallback'] = False
        def callback(url, params):
            raise http_error(403)
        h = FakeHTTP(callback)
        r = openreview(h, c, VENUE)
        self.assertTrue(r.failed)
        self.assertEqual(len(h.calls), 1)


class PersistenceHotfixTests(unittest.TestCase):
    def execute(self, result):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / 'data').mkdir()
            (root / 'config.yaml').write_text(yaml.safe_dump(CFG), encoding='utf-8')
            (root / 'overrides.yaml').write_text('[]', encoding='utf-8')
            (root / 'data/store.json').write_text(json.dumps({'papers': []}))
            (root / 'data/state.json').write_text(json.dumps({'sources': {
                'arXiv': {'last_success_at': '2026-09-10T00:00:00+00:00'}}}))
            with patch.object(run, 'ROOT', root), \
                 patch.object(run, 'build_jobs', return_value=[('arXiv', lambda: result)]), \
                 patch.object(run, 'build_site', return_value=root / 'index.html'), \
                 patch('sys.argv', ['run.py']):
                code = run.main()
            return code, json.loads((root / 'data/state.json').read_text()), \
                json.loads((root / 'data/store.json').read_text())

    def test_partial_failure_is_saved_but_exit_is_one(self):
        rec = make_record(source='arXiv', family='arXiv',
                          title='Reliable LLM agents', abstract='llm agents',
                          authors=['Fixture Author'], url='https://arxiv.org/abs/2610.00001',
                          arxiv_id='2610.00001', status='preprint', document_type='research_article')
        code, state, store = self.execute(Result(records=[rec], complete=False,
                                                note='test failure', failed=True))
        self.assertEqual(code, 1)
        self.assertEqual(state['last_run']['failed_sources'], 1)
        self.assertEqual(state['sources']['arXiv']['status'], 'partial')
        self.assertEqual(state['sources']['arXiv']['last_success_at'], '2026-09-10T00:00:00+00:00')
        self.assertEqual(len(store['papers']), 1)

    def test_all_failure_is_error(self):
        code, state, _ = self.execute(Result(complete=False, failed=True))
        self.assertEqual(code, 1)
        self.assertEqual(state['sources']['arXiv']['status'], 'error')

    def test_budget_limit_without_request_failure_is_partial_not_error(self):
        code, state, _ = self.execute(Result(complete=False, note='budget limited'))
        self.assertEqual(code, 0)
        self.assertEqual(state['sources']['arXiv']['status'], 'partial')
        self.assertEqual(state['sources']['arXiv']['last_success_at'], '2026-09-10T00:00:00+00:00')


if __name__ == '__main__':
    unittest.main()
