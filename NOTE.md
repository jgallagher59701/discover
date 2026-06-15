This is a start at a new crwler-based discovery system. Using Claude,
I started this an an alternative to an earlier crawler based on Nutch.
The link to a Claude 'project' in the OPeNDAP Claude account is:
https://claude.ai/chat/87b8ff27-7cf7-4510-9c93-0907758dfc8f.

User `conda activate discover` or `conda create -n discover duckdb scrapy`
to get a python enviroment to run this code.

To run the cc_dap_discover.py code, we need to authenticate against
the Common Crawl (CC) S3 bucket accessed using DuckDB.
* See https://duckdb.org/docs/stable/extensions/httpfs/s3api.html

jhrg 6/15/26
