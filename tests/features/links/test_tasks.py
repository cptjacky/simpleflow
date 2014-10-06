import unittest
import tempfile
import json
import shutil
from moto import mock_s3
import boto
import mock

from cdf.features.links.streams import (
    OutlinksRawStreamDef,
    OutlinksStreamDef,
    BadLinksStreamDef,
    BadLinksCountersStreamDef,
    OutlinksCountersStreamDef,
    OutcanonicalCountersStreamDef,
    OutredirectCountersStreamDef,
    LinksToNonStrategicStreamDef,
    LinksToNonStrategicCountersStreamDef
)
from cdf.features.links.tasks import (
    make_bad_link_file as compute_bad_link,
    make_links_counter_file as compute_link_counter,
    make_bad_link_counter_file as compute_bad_link_counter,
    make_top_domains_files as compute_top_domains,
    make_links_to_non_strategic_file,
    make_links_to_non_strategic_counter_file
)
from cdf.features.main.streams import InfosStreamDef, StrategicUrlStreamDef
from cdf.features.main.reasons import encode_reason_mask, REASON_HTTP_CODE
from cdf.testing.es_mock import (
    get_es_mget_mock,
    CRAWL_ID
)
from cdf.utils.s3 import list_files


# TODO(darkjh) remove duplication with stream-level test
class TestBadLinksTask(unittest.TestCase):
    def setUp(self):
        self.info = [
            [1, 0, '', 1, 12345, 200, 1, 1, 1],
            [2, 0, '', 1, 12345, 301, 1, 1, 1],
            [3, 0, '', 1, 12345, 500, 1, 1, 1],
        ]
        self.outlinks = [
            [4, 'a', 0, 1],
            [4, 'a', 0, 2],
            [5, 'a', 0, 1],
            [5, 'a', 0, 3],
            [6, 'canonical', 0, 2]
        ]
        self.tmp_dir = tempfile.mkdtemp()
        self.first_part_size = 2
        self.part_size = 1

    def tearDown(self):
        shutil.rmtree(self.tmp_dir)

    @mock_s3
    def test_harness(self):
        s3_uri = 's3://test_bucket'
        s3 = boto.connect_s3()
        s3.create_bucket('test_bucket')
        fake_crawl_id = 1234

        InfosStreamDef.persist(
            iter(self.info),
            s3_uri,
            first_part_size=self.first_part_size,
            part_size=self.part_size
        )

        OutlinksStreamDef.persist(
            iter(self.outlinks),
            s3_uri,
            first_part_size=self.first_part_size,
            part_size=self.part_size
        )

        compute_bad_link(
            fake_crawl_id, s3_uri,
            first_part_id_size=self.first_part_size,
            part_id_size=self.part_size
        )
        self.assertEqual(len(list_files(s3_uri, '.*badlinks.*')), 2)

        result = list(BadLinksStreamDef.load(s3_uri, tmp_dir=self.tmp_dir))
        expected = [
            [4, 2, 301],
            [5, 3, 500]
        ]
        self.assertEqual(result, expected)


class TestLinksCounterTask(unittest.TestCase):
    def setUp(self):
        self.outlinks = [
            [1, 'a', 0, 2, ''],
            [1, 'a', 1, 3, ''],
            [1, 'a', 0, 4, ''],
            [3, 'canonical', 5, 5, ''],
            [4, 'r301', 5, 5, ''],
        ]
        self.tmp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp_dir)

    @mock_s3
    def test_outgoing_harness(self):
        s3 = boto.connect_s3()
        s3.create_bucket('test_bucket')
        s3_uri = 's3://test_bucket'
        fake_crawl_id = 1234

        stream_args = {
            'uri': s3_uri,
            'tmp_dir': self.tmp_dir,
            'part_id': 0
        }

        OutlinksStreamDef.persist(
            iter(self.outlinks), s3_uri
        )

        compute_link_counter(
            fake_crawl_id, s3_uri,
            part_id=0, link_direction='out'
        )

        # check links
        expected = [
            [1, ['follow'], True, 2, 2], [1, ['link'], True, 1, 1]
        ]
        result = list(OutlinksCountersStreamDef.load(**stream_args))
        self.assertItemsEqual(expected, result)

        # check canonical
        expected = [[3, False]]
        result = list(OutcanonicalCountersStreamDef.load(**stream_args))
        self.assertItemsEqual(expected, result)

        # check redirection
        expected = [[4, True]]
        result = list(OutredirectCountersStreamDef.load(**stream_args))
        self.assertItemsEqual(expected, result)


class TestBadLinkCounterTask(unittest.TestCase):
    def setUp(self):
        self.badlinks = [
            [1, 2, 500],
            [1, 9, 500],
            [1, 2, 400],
        ]
        self.tmp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp_dir)

    @mock_s3
    def test_harness(self):
        s3 = boto.connect_s3()
        s3.create_bucket('test_bucket')
        s3_uri = 's3://test_bucket'
        part_id = 64
        fake_crawl_id = 1234

        BadLinksStreamDef.persist(
            iter(self.badlinks),
            s3_uri,
            part_id=part_id
        )

        compute_bad_link_counter(
            fake_crawl_id, s3_uri,
            part_id=part_id
        )

        result = list(BadLinksCountersStreamDef.load(
            s3_uri,
            tmp_dir=self.tmp_dir,
            part_id=part_id
        ))
        expected = [
            [1, 400, 1],  # 1 400 link
            [1, 500, 2],  # 2 500 link
        ]
        self.assertEqual(result, expected)


class TestMakeLinksToNonStrategicFile(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()

    @mock_s3
    def test_nominal_case(self):
        s3 = boto.connect_s3()
        bucket = s3.create_bucket('test_bucket')
        s3_uri = 's3://test_bucket'

        strategic_stream = iter([
            (1, True, encode_reason_mask()),
            (2, True, encode_reason_mask()),
            (3, False, encode_reason_mask(REASON_HTTP_CODE))
        ])
        StrategicUrlStreamDef.persist(
            strategic_stream,
            s3_uri
        )

        outlinks_stream = iter([
            (1, 'a', 0, 2),
            (1, 'a', 0, 3),
            (2, 'a', 0, 3)
        ])
        OutlinksStreamDef.persist(
            outlinks_stream,
            s3_uri
        )

        first_part_id_size = 2
        part_id_size = 10
        actual_result = make_links_to_non_strategic_file(
            s3_uri,
            first_part_id_size=first_part_id_size,
            part_id_size=part_id_size
        )

        expected_result = [
            "s3://test_bucket/url_non_strategic_links.txt.0.gz",
            "s3://test_bucket/url_non_strategic_links.txt.1.gz"
        ]
        self.assertEqual(expected_result, actual_result)

        actual_stream = list(
            LinksToNonStrategicStreamDef.load(s3_uri, self.tmp_dir)
        )

        expected_stream = [
            [1, 3],
            [2, 3]
        ]
        self.assertEqual(expected_stream, list(actual_stream))

class TestMakeLinksToNonStrategicCounterFile(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()

    @mock_s3
    def test_nominal_case(self):
        s3 = boto.connect_s3()
        bucket = s3.create_bucket('test_bucket')
        s3_uri = 's3://test_bucket'
        part_id = 3

        non_strategic_links_stream = iter([
            (1, 3),
            (1, 5),
            (3, 1),
            (5, 10),
            (5, 11),
            (5, 12)
        ])

        LinksToNonStrategicStreamDef.persist(
            non_strategic_links_stream,
            s3_uri,
            part_id=part_id
        )

        actual_result = make_links_to_non_strategic_counter_file(
            s3_uri,
            part_id
        )

        expected_result = "s3://test_bucket/url_non_strategic_links_counters.txt.3.gz"
        self.assertEqual(expected_result, actual_result)

        actual_stream = LinksToNonStrategicCountersStreamDef.load(
            s3_uri,
            self.tmp_dir
        )
        expected_stream = [
            [1, 2],
            [3, 1],
            [5, 3]
        ]

        self.assertEqual(expected_stream, list(actual_stream))

def _mock_es_handler_init(*args, **kwargs):
    mget_responses = {
            '1:0': ['url0'],
            '1:1': ['url1'],
            '1:2': ['url2'],
            '1:3': ['url3'],
            '1:4': ['url4'],
        }
    es_handler = mock.MagicMock()
    es_handler.mget = get_es_mget_mock(mget_responses)
    return es_handler


class TestMakeTopDomainsFiles(unittest.TestCase):
    @mock_s3
    @mock.patch(
        'cdf.features.links.tasks.EsHandler',
        _mock_es_handler_init
    )
    def test_nominal_case(self):
        #mock
        s3 = boto.connect_s3()
        bucket = s3.create_bucket('test_bucket')
        s3_uri = "s3://test_bucket"

        externals = iter([
            [0, "a", 0, -1, "http://foo.com/bar.html"],
            [0, "a", 0, -1, "http://bar.com/image.jpg"],
            [3, "a", 0, -1, "http://foo.com/qux.css"],
            [3, "canonical", 0, 0],  # canonical
            [4, "a", 0, -1, "http://bar.foo.com/baz.html"],
            [4, "a", 0, -1, "http://bar.com/baz.html"],
            [4, "a", 0, 3],  # internal link
            [4, "a", 0, -1, "http://foo.com/"],
            [4, "a", 0, -1, "foo"],  # invalid url
        ])
        OutlinksRawStreamDef.persist(externals, s3_uri)

        #actual call
        nb_top_domains = 10
        actual_result = compute_top_domains(
            CRAWL_ID,
            s3_uri,
            nb_top_domains,
            "mock_es_location",
            "mock_es_index",
            "mock_es_doc_type"
        )

        #check file uris
        expected_result = [
            "s3://test_bucket/top_full_domains.json",
            "s3://test_bucket/top_second_level_domains.json"
        ]
        self.assertListEqual(expected_result, actual_result)

        #check file contents
        expected_top_domains = [
            {
                "domain": "foo.com",
                "follow_links": 3,
                "nofollow_links": 0,
                "unique_follow_links": 3,
                "follow_samples": [
                    {"url": "http://foo.com/", "unique_links": 1, "sources": ["url4"]},
                    {"url": "http://foo.com/bar.html", "unique_links": 1, "sources": ["url0"]},
                    {"url": "http://foo.com/qux.css", "unique_links": 1, "sources": ["url3"]}
                ],
                "nofollow_samples": []
            },
            {
                "domain": "bar.com",
                "follow_links": 2,
                "nofollow_links": 0,
                "unique_follow_links": 2,
                "follow_samples": [
                    {"url": "http://bar.com/baz.html", "unique_links": 1, "sources": ["url4"]},
                    {"url": "http://bar.com/image.jpg", "unique_links": 1, "sources": ["url0"]}
                ],
                "nofollow_samples": []
            },
            {
                "domain": "bar.foo.com",
                "follow_links": 1,
                "nofollow_links": 0,
                "unique_follow_links": 1,
                "follow_samples": [
                    {"url": "http://bar.foo.com/baz.html", "unique_links": 1, "sources": ["url4"]},
                ],
                "nofollow_samples": []
            }
        ]

        k = bucket.get_key("top_full_domains.json")
        actual_top_domains = json.loads(k.get_contents_as_string())
        self.assertEqual(expected_top_domains, actual_top_domains)

        expected_top_second_level_domains = [
            {
                "domain": "foo.com",
                "follow_links": 4,
                "nofollow_links": 0,
                "unique_follow_links": 4,
                "follow_samples": [
                    {"url": "http://bar.foo.com/baz.html", "unique_links": 1, "sources": ["url4"]},
                    {"url": "http://foo.com/", "unique_links": 1, "sources": ["url4"]},
                    {"url": "http://foo.com/bar.html", "unique_links": 1, "sources": ["url0"]},
                    {"url": "http://foo.com/qux.css", "unique_links": 1, "sources": ["url3"]}
                ],
                "nofollow_samples": []
            },
            {
                "domain": "bar.com",
                "follow_links": 2,
                "nofollow_links": 0,
                "unique_follow_links": 2,
                "follow_samples": [
                    {"url": "http://bar.com/baz.html", "unique_links": 1, "sources": ["url4"]},
                    {"url": "http://bar.com/image.jpg", "unique_links": 1, "sources": ["url0"]}
                ],
                "nofollow_samples": []
            }
        ]

        k = bucket.get_key("top_second_level_domains.json")
        actual_top_second_level_domains = json.loads(k.get_contents_as_string())
        self.assertEqual(
            expected_top_second_level_domains,
            actual_top_second_level_domains
        )