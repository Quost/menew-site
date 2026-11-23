#!/usr/bin/env python3
"""Checks a generated site before it can be deployed (scripts/build.py runs this at the end).

    python3 scripts/check.py public

Fails (exit 1, with a list) when: a page or language is missing; a placeholder was left unfilled; an internal link,
image or video points to nothing; a page links to the old menew.web.app / menew-prd.web.app addresses; a canonical,
hreflang or html lang is wrong; structured data is not valid JSON; the sitemap lists a page that does not exist or is
noindex (or misses one that is indexable); robots.txt, llms.txt, build.json, the IndexNow key file or an og image is missing.
"""
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from content import LANGS, ROOT

SITE = "https://menew.pururucos.com.br"
INDEXNOW_KEY = "df850856e8360dcdd128c85eb1c159fd"
HREFLANG = {"en": "en", "pt": "pt-BR", "de": "de", "es": "es", "zh": "zh-Hans"}
OLD_HOSTS = ("menew.web.app", "menew-prd.web.app")
PAGES = ("", "mac", "linux", "windows")


def page_path(lang, page):
    if lang == "en":
        return f"/{page}" if page else "/"
    return f"/{lang}/{page}" if page else f"/{lang}"


def file_for(out, urlpath):
    """The file Firebase Hosting (cleanUrls) serves for a local path, or None"""
    rel = urlpath.lstrip("/")
    target = out / rel
    if rel and target.is_file():
        return target
    if (target / "index.html").is_file():
        return target / "index.html"
    if rel and (out / (rel + ".html")).is_file():
        return out / (rel + ".html")
    return None


def url_of(out, html_file):
    rel = html_file.relative_to(out).as_posix()
    rel = rel[:-len("index.html")].rstrip("/") if rel.endswith("index.html") else rel
    return SITE + "/" + rel if rel else SITE + "/"


def check(out):
    out = Path(out)
    problems = []
    compare = json.loads((ROOT / "site/compare.json").read_text(encoding="utf-8"))

    expected = [page_path(lang, page) for page in PAGES for lang in LANGS] + ["/vs"] + [f"/vs/{s}" for s in compare]
    for p in expected:
        if not file_for(out, p):
            problems.append(f"page {p} was not generated")

    noindex, html_files = set(), sorted(out.rglob("*.html"))
    for f in html_files:
        rel = f.relative_to(out).as_posix()
        text = f.read_text(encoding="utf-8")
        if "{{" in text:
            problems.append(f"{rel}: unfilled placeholder {re.search(r'{{[^}]*}}', text).group(0)}")
        for host in OLD_HOSTS:
            if host in text:
                problems.append(f"{rel}: links to the old address {host}")
        for ref in re.findall(r'(?:src|href|poster)="(/(?!/)[^"#?]*)', text):
            if not file_for(out, ref):
                problems.append(f"{rel}: {ref} does not exist")
        for block in re.findall(r'<script type="application/ld\+json">(.*?)</script>', text, re.S):
            try:
                json.loads(block)
            except json.JSONDecodeError as e:
                problems.append(f"{rel}: structured data is not valid JSON ({e})")
        page_url = url_of(out, f)
        if re.search(r'<meta name="robots" content="noindex', text):
            noindex.add(page_url)
        canon = re.search(r'<link rel="canonical" href="([^"]+)">', text)
        if not canon:
            problems.append(f"{rel}: no canonical link")
        elif canon.group(1) != page_url:
            problems.append(f"{rel}: canonical is {canon.group(1)}, expected {page_url}")

    # Language versions: html lang, the 5 hreflang links plus x-default, on every page of every language
    for page in PAGES:
        for lang in LANGS:
            f = file_for(out, page_path(lang, page))
            if not f:
                continue
            text = f.read_text(encoding="utf-8")
            if f'<html lang="{HREFLANG[lang]}"' not in text:
                problems.append(f"{page_path(lang, page)}: html lang is not {HREFLANG[lang]}")
            for other in LANGS:
                link = SITE + page_path(other, page) if page_path(other, page) != "/" else SITE + "/"
                if f'hreflang="{HREFLANG[other]}" href="{link}"' not in text:
                    problems.append(f"{page_path(lang, page)}: missing hreflang {HREFLANG[other]} -> {link}")
            default = SITE + (page_path("en", page) if page else "/")
            if f'hreflang="x-default" href="{default}"' not in text:
                problems.append(f"{page_path(lang, page)}: missing hreflang x-default -> {default}")
            og = re.search(r'<meta property="og:image" content="([^"]+)">', text)
            if not og or not og.group(1).startswith(SITE + "/assets/og/") or not file_for(out, og.group(1)[len(SITE):]):
                problems.append(f"{page_path(lang, page)}: og:image missing or not found")

    for lang in LANGS:
        if not (out / f"assets/og/{lang}.png").is_file():
            problems.append(f"assets/og/{lang}.png is missing")

    # Sitemap: every URL exists and is indexable; every indexable page is listed
    sitemap = out / "sitemap.xml"
    if not sitemap.is_file():
        problems.append("sitemap.xml is missing")
    else:
        ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
        try:
            locs = [e.text for e in ET.parse(sitemap).getroot().findall("s:url/s:loc", ns)]
        except ET.ParseError as e:
            problems.append(f"sitemap.xml is not valid XML ({e})")
            locs = []
        for loc in locs:
            if not loc.startswith(SITE + "/") or not file_for(out, loc[len(SITE):] or "/"):
                problems.append(f"sitemap.xml lists {loc}, which does not exist")
            if loc in noindex:
                problems.append(f"sitemap.xml lists {loc}, which is noindex")
        if len(locs) != len(set(locs)):
            problems.append("sitemap.xml lists a URL twice")
        for f in html_files:
            u = url_of(out, f)
            if u not in noindex and u not in locs:
                problems.append(f"{u} is indexable but not in sitemap.xml")

    robots = out / "robots.txt"
    if not robots.is_file() or f"Sitemap: {SITE}/sitemap.xml" not in robots.read_text(encoding="utf-8"):
        problems.append("robots.txt is missing or does not point to the sitemap")
    llms = out / "llms.txt"
    if not llms.is_file() or not llms.read_text(encoding="utf-8").startswith("# Menew"):
        problems.append("llms.txt is missing or empty")
    key = out / f"{INDEXNOW_KEY}.txt"
    if not key.is_file() or key.read_text(encoding="utf-8").strip() != INDEXNOW_KEY:
        problems.append(f"IndexNow key file {INDEXNOW_KEY}.txt is missing or wrong")
    try:
        info = json.loads((out / "build.json").read_text(encoding="utf-8"))
        if not re.fullmatch(r"[0-9a-f]{64}", info.get("fingerprint", "")):
            problems.append("build.json has no fingerprint")
    except (OSError, json.JSONDecodeError) as e:
        problems.append(f"build.json is missing or invalid ({e})")
    for f in list(out.rglob("*.txt")) + list(out.rglob("*.xml")) + list(out.rglob("*.json")):
        text = f.read_text(encoding="utf-8", errors="replace")
        for host in OLD_HOSTS:
            if host in text:
                problems.append(f"{f.relative_to(out).as_posix()}: mentions the old address {host}")
    return problems


def main(argv):
    if len(argv) != 2:
        print(__doc__.strip())
        return 2
    problems = check(argv[1])
    if problems:
        print("\n".join(f"  - {p}" for p in problems), file=sys.stderr)
        print(f"{len(problems)} problem(s)", file=sys.stderr)
        return 1
    print("checks: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
