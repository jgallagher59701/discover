import sys
from pathlib import Path

import pytest
from scrapy.http import TextResponse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dap_spider import DapSpider  # noqa: E402  (needs sys.path insert above)


def make_response(url, body="", status=200, headers=None):
    """Build a real scrapy TextResponse for feeding into spider callbacks
    directly, without a Crawler/downloader in the loop."""
    return TextResponse(
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
