#!/usr/bin/env python3
"""Tells IndexNow (Bing, Yandex, Naver, Seznam…) that the site changed, so pages are recrawled in hours, not weeks.
Bing also feeds ChatGPT search. Run only after a successful deploy:

    python3 scripts/indexnow.py public

Sends every URL of public/sitemap.xml plus /llms.txt and /sitemap.xml. The key is public by design: it is served at
https://menew.pururucos.com.br/df850856e8360dcdd128c85eb1c159fd.txt (static/). Never fails the workflow: a ping that
does not go through only prints a warning.
"""
import json
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

HOST = "menew.pururucos.com.br"
KEY = "df850856e8360dcdd128c85eb1c159fd"


def main(argv):
    out = Path(argv[1] if len(argv) > 1 else "public")
    ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    urls = [e.text for e in ET.parse(out / "sitemap.xml").getroot().findall("s:url/s:loc", ns)]
    urls += [f"https://{HOST}/llms.txt", f"https://{HOST}/sitemap.xml"]
    body = json.dumps({"host": HOST, "key": KEY, "keyLocation": f"https://{HOST}/{KEY}.txt", "urlList": urls}).encode()
    req = urllib.request.Request("https://api.indexnow.org/indexnow", data=body, method="POST",
                                 headers={"Content-Type": "application/json; charset=utf-8", "User-Agent": "menew-site-build"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            print(f"IndexNow: HTTP {r.status} ({len(urls)} URLs)")
    except urllib.error.HTTPError as e:
        print(f"::warning::IndexNow: HTTP {e.code} {e.read()[:200]!r}")
    except (urllib.error.URLError, TimeoutError) as e:
        print(f"::warning::IndexNow: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
