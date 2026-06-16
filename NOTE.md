This is a start at a new crawler-based discovery system. Using Claude,
I started this an an alternative to an earlier crawler based on Nutch.
The link to a Claude 'project' in the OPeNDAP Claude account is:
https://claude.ai/chat/87b8ff27-7cf7-4510-9c93-0907758dfc8f.

User `conda activate discover` or `conda create -n discover duckdb scrapy`
to get a python environment to run this code.

To run the cc_dap_discover.py code, we need to authenticate against
the Common Crawl (CC) S3 bucket accessed using DuckDB.
* See https://duckdb.org/docs/stable/extensions/httpfs/s3api.html
This can be done using anonymous credentials since the CC data is in
an open S3 bucket.

## ABout various searches of the Common Crawl (CC) database

For these, the .csv and .txt are from the same run, so I'll only list
the csv file

| Name | lines | What |
|--|--|--|
| candidate_urls.2026.21.csv | 11,914 | Initial crawl, limited to org and edu and  no contents.html|catalog.xml in the regex |
| candidate_urls.more_patterns.csv | 22,880 | Added more patterns and used the more more expansive TLDs ('edu', 'org', 'gov', 'mil' and %.ac.%, ...) |
| candidate_urls.all_TLDs.csv | 44,275 | Expansive patters, all TLDs |

jhrg 6/15/26
