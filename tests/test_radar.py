import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import yaml
from radar.core import Store, arxiv_of, classify, iso, make_record, rank, safe_url
from radar.collect import (Result, parse_feed, parse_acl, parse_openreview, parse_crossref,
                           parse_article_html, parse_pmlr_index, parse_cvf_index)
import run

ROOT=Path(__file__).resolve().parents[1]
CFG=yaml.safe_load((ROOT/'config.yaml').read_text())

def rec(**kwargs):
    base=dict(source='arXiv',family='arXiv',title='Reliable LLM agents under uncertainty',
              url='https://arxiv.org/abs/2601.12345',authors=['Alice Smith'],abstract='value of information',
              published='2026-01-02',arxiv_id='2601.12345',status='preprint',document_type='research_article')
    base.update(kwargs)
    return make_record(**base)

class CoreTests(unittest.TestCase):
    def test_arxiv_version_normalization(self):
        self.assertEqual(arxiv_of('https://arxiv.org/abs/2601.12345v2'),'2601.12345')
    def test_no_invented_date(self):
        self.assertEqual(iso(''),'')
        self.assertEqual(iso('2026-07'),'2026-07')
        self.assertEqual(iso('2026'),'2026')
        self.assertEqual(iso('not a date'),'')
    def test_safe_url(self):
        self.assertEqual(safe_url('javascript:alert(1)'),'')
    def test_keyword_boundaries(self):
        self.assertEqual(rank('reagents and calibrationism','',CFG)[0],0)
        self.assertGreater(rank('LLM agents','',CFG)[0],0)
    def test_news_not_research(self):
        self.assertEqual(classify('', 'AI research', '10.1038/d41586-026-12345'),'commentary')
        self.assertEqual(classify('journal-article'),'unspecified')
    def test_version_dedup(self):
        s=Store({'papers':[]},CFG)
        s.ingest([rec()], '2026-01-03')
        s.ingest([rec(url='https://arxiv.org/abs/2601.12345v2',arxiv_id='2601.12345v2',updated='2026-02-01')], '2026-02-02')
        self.assertEqual(len(s.papers),1)
        p=next(iter(s.papers.values()))
        self.assertEqual(p['first_seen_at'],'2026-01-03')
        self.assertEqual(p['record_changed_at'],'2026-02-02')
    def test_unchanged_does_not_become_new(self):
        s=Store({'papers':[]},CFG)
        s.ingest([rec()], '2026-01-03')
        self.assertEqual(s.ingest([rec()], '2026-01-04'),(0,0))
        self.assertEqual(next(iter(s.papers.values()))['record_changed_at'],'2026-01-03')
    def test_merge_preprint_and_published_keeps_status(self):
        s=Store({'papers':[]},CFG)
        s.ingest([rec()], '2026-01-03')
        s.ingest([rec(source='Nature',family='Nature',url='https://doi.org/10.1000/example',doi='10.1000/example',arxiv_id='',status='published',published='2026-04-03')], '2026-04-04')
        self.assertEqual(len(s.papers),1)
        s.ingest([rec(updated='2026-05-01')], '2026-05-02')
        p=next(iter(s.papers.values()))
        self.assertEqual(p['status'],'published')
        self.assertTrue(p['preprint_at'].startswith('2026-01-02'))
        self.assertTrue(p['published_at'].startswith('2026-04-03'))
    def test_distinct_authors_not_merged_by_title(self):
        s=Store({'papers':[]},CFG)
        s.ingest([rec(),rec(url='https://a.example/p2',arxiv_id='',authors=['Bob Jones'])],'2026-01-03')
        self.assertEqual(len(s.papers),2)
    def test_code_link_is_not_verification(self):
        s=Store({'papers':[]},CFG)
        s.ingest([rec(code=['https://github.com/user/project'])],'2026-01-03')
        p=next(iter(s.papers.values()))
        self.assertEqual(p['code_status'],'candidate')
        s.apply_overrides([{'match':'arxiv:2601.12345','official_code':True,'code_url':'https://github.com/user/project'}])
        self.assertEqual(p['code_status'],'candidate')
        s.apply_overrides([{'match':'arxiv:2601.12345','official_code':True,'code_url':'https://github.com/user/project','evidence_url':'https://publisher.example/article'}])
        self.assertEqual(p['code_status'],'verified')

class ParserTests(unittest.TestCase):
    def test_atom(self):
        xml=b'''<feed xmlns="http://www.w3.org/2005/Atom" xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/"><opensearch:totalResults>1</opensearch:totalResults><entry><id>http://arxiv.org/abs/2601.12345v2</id><title>LLM agents</title><summary>value of information</summary><author><name>Alice Smith</name></author><published>2026-01-02T00:00:00Z</published><updated>2026-02-01T00:00:00Z</updated><link href="https://arxiv.org/abs/2601.12345v2" rel="alternate"/><link href="https://arxiv.org/pdf/2601.12345v2" type="application/pdf"/></entry></feed>'''
        r,total=parse_feed(xml,'arXiv','arXiv',True)
        self.assertEqual(total,1)
        self.assertEqual(r[0]['arxiv_id'],'2601.12345')
        self.assertEqual(r[0]['authors'],['Alice Smith'])
        self.assertTrue(r[0]['pdf_url'])
    def test_rdf_rss(self):
        xml=b'''<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns="http://purl.org/rss/1.0/" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:prism="http://prismstandard.org/namespaces/basic/2.0/"><item><title>LLM agents</title><link>https://www.nature.com/articles/example</link><description>scientific discovery</description><dc:creator>Alice Smith</dc:creator><dc:date>2026-10-01</dc:date><prism:doi>10.1000/example</prism:doi><prism:section>Article</prism:section></item></rdf:RDF>'''
        rows,_=parse_feed(xml,'Nature','Nature')
        self.assertEqual(rows[0]['document_type'],'research_article')
        self.assertEqual(rows[0]['doi'],'10.1000/example')
    def test_error_feed_not_empty_success(self):
        with self.assertRaises(ValueError):parse_feed(b'<html><title>Captcha</title></html>','X','X')
    def test_openreview_acceptance_not_submission(self):
        venue='ICLR.cc/2026/Conference'
        note={'id':'abc','cdate':1700000000000,'mdate':1710000000000,'content':{'title':{'value':'LLM agents'},'venueid':{'value':venue+'/Submission'},'authors':{'value':['Alice Smith']}}}
        self.assertIsNone(parse_openreview(note,venue))
        note['content']['venueid']['value']=venue
        p=parse_openreview(note,venue)
        self.assertEqual(p['status'],'accepted')
        self.assertEqual(p['published_at'],'')
    def test_crossref_type_conservative(self):
        p=parse_crossref({'title':['AI agents'],'DOI':'10.1000/test','type':'journal-article','published':{'date-parts':[[2026,7]]}},{'name':'Science','family':'Science'})
        self.assertEqual(p['published_at'],'2026-07')
        self.assertEqual(p['document_type'],'unspecified')
    def test_acl_excludes_frontmatter_and_workshops(self):
        data=b'''<collection id="2026.acl"><volume id="long"><meta><booktitle>ACL 2026</booktitle><year>2026</year><month>7</month></meta><paper id="0"><title>Front matter</title></paper><paper id="1"><title>Reliable <fixed-case>LLM</fixed-case> agents</title><author><first>Alice</first><last>Smith</last></author><abstract>optimal stopping</abstract><doi>10.1000/acl</doi></paper></volume><volume id="workshop"><paper id="1"><title>Other</title></paper></volume></collection>'''
        rows=parse_acl(data,'2026.acl',['long','short','main'])
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['title'],'Reliable LLM agents')
        self.assertEqual(rows[0]['published_at'],'2026-07')
    def test_pmlr_index_and_detail(self):
        data='<div class="paper"><p class="title">LLM agents</p><p class="links"><a href="/v306/a26.html">abs</a><a href="a.pdf">pdf</a></p></div>'
        rows=parse_pmlr_index(data,'https://proceedings.mlr.press/v306/')
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0][1],'https://proceedings.mlr.press/v306/a26.html')
        detail='<meta name="citation_title" content="LLM agents"><meta name="citation_author" content="Alice Smith"><meta name="citation_date" content="2026/07/06"><div id="abstract">optimal stopping</div>'
        r=parse_article_html(detail,rows[0][1],'PMLR','ICML 2026')
        self.assertEqual(r['authors'],['Alice Smith'])
        self.assertEqual(r['abstract'],'optimal stopping')
    def test_cvf_index(self):
        rows=parse_cvf_index('<dl><dt class="ptitle"><a href="/content/CVPR2026/html/test.html">LLM agents</a></dt><dd>Alice Smith</dd></dl>','https://openaccess.thecvf.com/CVPR2026?day=all')
        self.assertEqual(len(rows),1)
        self.assertTrue(rows[0][1].startswith('https://openaccess.thecvf.com/content/'))

class BuildTests(unittest.TestCase):
    def test_script_injection_escaped(self):
        s=Store({'papers':[]},CFG)
        r=rec(abstract='LLM agents')
        s.ingest([r],'2026-01-03')
        next(iter(s.papers.values()))['abstract']='</script><script>alert(1)</script>'
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'web').mkdir()
            for f in ['index.html','style.css','app.js']:(root/'web'/f).write_text((ROOT/'web'/f).read_text())
            with patch.object(run,'ROOT',root):out=run.build_site(s,CFG,{'sources':{}})
            h=out.read_text()
            self.assertNotIn('</script><script>alert(1)</script>',h)
            self.assertIn('\\u003c/script\\u003e',h)
    def test_failure_preserves_data_and_watermark(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'data').mkdir();(root/'config.yaml').write_text(yaml.safe_dump(CFG));(root/'overrides.yaml').write_text('[]')
            s=Store({'papers':[]},CFG);s.ingest([rec()],'2026-01-03')
            (root/'data/store.json').write_text(json.dumps(s.payload()))
            (root/'data/state.json').write_text(json.dumps({'sources':{'bad':{'last_success_at':'2026-01-03T00:00:00+00:00'}}}))
            def broken():raise RuntimeError('offline test')
            with patch.object(run,'ROOT',root),patch.object(run,'build_jobs',return_value=[('bad',broken)]),patch.object(run,'build_site',return_value=root/'index.html'),patch('sys.argv',['run.py']):
                self.assertEqual(run.main(),1)
            data=json.loads((root/'data/store.json').read_text());state=json.loads((root/'data/state.json').read_text())
            self.assertEqual(len(data['papers']),1)
            self.assertEqual(state['sources']['bad']['last_success_at'],'2026-01-03T00:00:00+00:00')
            self.assertEqual(state['sources']['bad']['status'],'error')

if __name__=='__main__':unittest.main()
