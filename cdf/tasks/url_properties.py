import os
import gzip
import lz4
import json
from pandas import HDFStore

from cdf.streams.constants import STREAMS_HEADERS, STREAMS_FILES
from cdf.streams.caster import Caster
from cdf.streams.utils import split_file, split
from cdf.collections.url_properties.generator import UrlPropertiesGenerator
from cdf.collections.properties_stats.aggregator import PropertiesStatsAggregator, PropertiesStatsConsolidator
from cdf.utils.s3 import fetch_files, push_content, push_file


def compute_properties_from_s3(crawl_id, part_id, rev_num, s3_uri, settings, tmp_dir_prefix='/tmp', force_fetch=False):
    """
    Match all urls from a crawl's `part_id` to properties defined by rules in a `settings` dictionnary and save it to a S3 bucket.

    Crawl dataset for this part_id is found by fetching all files finishing by .txt.[part_id] in the `s3_uri` called.

    :param part_id : the part_id from the crawl
    :param s3_uri : the location where the file will be pushed. filename will be url_properties.txt.[part_id]
    :param tmp_dir : the temporary directory where the S3 files will be put to compute the task
    :param force_fetch : fetch the S3 files even if they are already in the temp directory
    """

    # Fetch locally the files from S3
    tmp_dir = os.path.join(tmp_dir_prefix, 'crawl_%d' % crawl_id)

    files_fetched = fetch_files(s3_uri, tmp_dir, regexp=['urlids.txt.%d.gz' % part_id], force_fetch=force_fetch)
    path_local, fetched = files_fetched[0]

    cast = Caster(STREAMS_HEADERS['PATTERNS']).cast
    stream_patterns = cast(split_file(gzip.open(path_local)))

    g = UrlPropertiesGenerator(stream_patterns, settings)

    map_func = lambda k: '\t'.join((str(k[0]), k[1]['resource_type']))
    content = '\n'.join(map(map_func, g))

    encoded_content = lz4.dumps(content)
    push_content(os.path.join(s3_uri, 'url_properties.rev%d.txt.%d.lz4' % (rev_num, part_id)), encoded_content)


def compute_properties_stats_counter_from_s3(crawl_id, part_id, rev_num, s3_uri, tmp_dir_prefix='/tmp', force_fetch=False):
    # Fetch locally the files from S3
    tmp_dir = os.path.join(tmp_dir_prefix, 'crawl_%d' % crawl_id)

    files_fetched = fetch_files(s3_uri,
                                tmp_dir,
                                regexp=['url(ids|infos|links|inlinks).txt.%d.gz' % part_id, 'url_properties.rev%d.txt.%d.lz4' % (rev_num, part_id)],
                                force_fetch=force_fetch)
    streams = {}

    for path_local, fetched in files_fetched:
        stream_identifier = STREAMS_FILES[os.path.basename(path_local).split('.')[0]]
        cast = Caster(STREAMS_HEADERS[stream_identifier.upper()]).cast
        if stream_identifier == "properties":
            streams["stream_properties"] = cast(split(lz4.loads(open(path_local).read()).split('\n')))
        else:
            streams["stream_%s" % stream_identifier] = cast(split_file(gzip.open(path_local)))

    aggregator = PropertiesStatsAggregator(**streams)
    content = json.dumps(aggregator.get())
    push_content(os.path.join(s3_uri, 'properties_stats_partial_rev%d/stats.json.%d' % (rev_num, part_id)), content)


def consolidate_properties_stats_counter_from_s3(crawl_id, rev_num, s3_uri, tmp_dir_prefix='/tmp', force_fetch=False):
    # Fetch locally the files from S3
    tmp_dir = os.path.join(tmp_dir_prefix, 'crawl_%d' % crawl_id)

    files_fetched = fetch_files(s3_uri,
                                tmp_dir,
                                regexp=['properties_stats_partial_rev%d/stats.json' % rev_num],
                                force_fetch=force_fetch)

    counters = [json.loads(open(path_local).read()) for path_local, fetched in files_fetched]
    c = PropertiesStatsConsolidator(counters)
    df = c.get_dataframe()

    h5_file = os.path.join(tmp_dir, 'properties_stats_rev%d.h5' % rev_num)
    store = HDFStore(h5_file, complevel=9, complib='blosc')
    store['stats'] = df
    store.close()

    push_file(os.path.join(s3_uri, 'properties_stats_rev%d.h5' % rev_num), h5_file)
