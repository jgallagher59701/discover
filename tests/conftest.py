import sys
from pathlib import Path

import pytest
from scrapy.http import TextResponse, XmlResponse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dap_spider import DapSpider  # noqa: E402  (needs sys.path insert above)


def make_response(url, body="", status=200, headers=None):
    """Build a real scrapy TextResponse for feeding into spider callbacks
    directly, without a Crawler/downloader in the loop.

    Fine for on_dmr/on_dds (they only inspect response.text/headers), but
    NOT for parse_thredds_catalog: a plain TextResponse's .selector defaults
    to an HTML parser, which silently breaks namespace-based XPath matching
    on XML bodies. Use make_xml_response for that callback instead."""
    return TextResponse(
        url=url,
        status=status,
        headers=headers or {},
        body=body.encode("utf-8"),
    )


def make_xml_response(url, body="", status=200, headers=None):
    """Build a real scrapy XmlResponse, matching what Scrapy's own
    content-type/URL sniffing constructs in production for .xml catalog
    responses. Required for parse_thredds_catalog's namespace-based XPath
    matching to behave as it does against real hosts."""
    return XmlResponse(
        url=url,
        status=status,
        headers=headers or {},
        body=body.encode("utf-8"),
    )


@pytest.fixture
def spider():
    """A DapSpider instance with no Crawler attached, for calling its bound
    callback methods directly against synthetic responses."""
    return DapSpider(seeds_file="unused")
