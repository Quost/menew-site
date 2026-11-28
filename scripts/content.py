#!/usr/bin/env python3
"""The content contract: how an app repository gives its page texts and screenshots to this site.

Each app repository (Quost/menew, Quost/menew-linux, Quost/menew-windows) has a `site/` folder with:

    site/content.json   {"contract": 1, "platform": "<id>", "assets": "<optional path>", "texts": {"en": {...}, ...}}
    site/assets/        screenshots (optional; "assets" in content.json can point elsewhere in the repo)

See the README ("Content contract") for the full description. This module loads and validates that folder.
It is also a command-line validator, used by the app repositories' CI:

    python3 scripts/content.py mac ../menew/site
    python3 scripts/content.py linux ../menew-linux/site

Exit code 0 when the content is valid, 1 with a list of every problem otherwise. Python standard library only.
"""
from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONTRACT = 1
LANGS = ("en", "pt", "de", "es", "zh")
# File types copied from the assets folder (English at the top level, other languages in <lang>/ subfolders)
ASSET_TYPES = {".png", ".webp", ".jpg", ".jpeg", ".gif", ".svg", ".mp4", ".webm"}
# Texts can carry a little inline HTML; anything else is a mistake (or an injection) and fails the build
ALLOWED_TAGS = {"b", "strong", "i", "em", "code", "kbd", "span", "br", "small", "sup", "sub", "a"}
ALLOWED_ATTRS = {"class", "href", "title", "lang"}
# Placeholders the build fills itself: a content file never needs to provide these
COMPUTED = {
    "html_lang", "url", "hreflang", "shots", "home", "mac_path", "linux_path", "windows_path", "og_image", "og_locale",
    "lang_switch", "style", "analytics", "jsonld", "family_band", "family_credit", "js_strings",
    "release_url", "release_version", "badge_label", "palette_cards", "clipboard_cards", "tools_cards", "next_cards",
    "page_title", "page_desc", "robots", "win_badge",
    "deb_url", "deb_name", "appimage_url", "appimage_name",
}


class ContentError(Exception):
    """Every problem found in one content folder, with a message meant for the person who maintains it."""

    def __init__(self, label, problems):
        self.problems = problems
        super().__init__(f"{label}: invalid site content\n" + "\n".join(f"  - {p}" for p in problems))


@dataclass
class Content:
    platform: str
    site_dir: Path
    texts: dict                                   # {lang: {key: text}}
    assets_dir: Path | None                       # None when the repo has no screenshots (yet)
    assets: list = field(default_factory=list)    # paths relative to assets_dir, e.g. "palette-dark.png", "pt/palette-dark.png"
    source: str = ""                              # "Quost/menew@<sha>", "local ../menew/site", "fallback"


def load_config():
    return json.loads((ROOT / "platforms.json").read_text(encoding="utf-8"))


def read_text(path):
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def placeholders(text):
    return set(re.findall(r"\{\{(\w+)\}\}", text))


def template(name):
    return read_text(ROOT / "site/templates" / name)


def required_keys(platform, config=None):
    """Keys every language of a platform's content must have: the template's placeholders that the build does not fill
    and that do not come from the shared strings, plus the keys the build reads directly (platforms.json "required")."""
    cfg = (config or load_config())["platforms"][platform]
    keys = placeholders(template(cfg["template"])) - COMPUTED - set(cfg["shared"])
    return sorted(keys | set(cfg["required"]))


def shot_refs(platform, config=None):
    """Screenshot names that must exist for every language: {{shots}}/<name> in the templates whose screenshots come
    from this platform, plus the names listed for the structured data. Returns (per_language, english_only)."""
    config = config or load_config()
    cfg = config["platforms"][platform]
    templates = [cfg["template"]] + ([config["overview"]["template"]] if config["overview"]["shots"] == platform else [])
    per_lang = set()
    for name in templates:
        per_lang |= set(re.findall(r"\{\{shots\}\}/([^\"'\s)]+)", template(name)))
    per_lang |= {f"{n}-dark.png" for n in cfg["screenshots"]}
    # Absolute links into this platform's asset folder (e.g. /assets/icon.png for the Mac) in any template
    english = set()
    prefix = cfg["assets_url"].rstrip("/") + "/"
    static = ROOT / "static"
    for tpl in (ROOT / "site/templates").glob("*.html"):
        for ref in re.findall(r'(?:src|href|poster)="(/assets/[^"#?]+)"', read_text(tpl)):
            rest = ref[len(prefix):] if ref.startswith(prefix) else None
            if rest and "/" not in rest and not (static / ref.lstrip("/")).exists():
                english.add(rest)
    return sorted(per_lang), sorted(english)


TAG = re.compile(r"<\s*(/?)\s*([a-zA-Z][a-zA-Z0-9]*)([^<>]*)>")
ATTR = re.compile(r'([a-zA-Z_:-]+)\s*=\s*"([^"]*)"')
# Links: https://, a site path (but not //host or /\host, which browsers treat as another site) or an anchor
SAFE_HREF = re.compile(r"(https://|/(?![/\\])|#)")


def check_html(text):
    """Problems with the inline HTML of one text (empty list when it is fine). Texts also land inside attributes
    (alt="...", content="..."), so a raw " or a < that does not open a complete allowed tag is rejected too."""
    problems = []
    outside = []      # the text between the tags
    pos = 0
    for m in TAG.finditer(text):
        outside.append(text[pos:m.start()])
        pos = m.end()
        tag, attrs = m.group(2).lower(), m.group(3)
        if tag not in ALLOWED_TAGS:
            problems.append(f"tag <{tag}> is not allowed (allowed: {', '.join(sorted(ALLOWED_TAGS))})")
            continue
        if ATTR.sub("", attrs).strip(" /"):
            problems.append(f"<{tag}> attributes must be written as name=\"value\"")
        for name, value in ATTR.findall(attrs):
            if name.lower() not in ALLOWED_ATTRS:
                problems.append(f"attribute {name}= is not allowed in <{tag}>")
            elif name.lower() == "href" and not SAFE_HREF.match(value):
                problems.append(f"link {value!r} must start with https://, / (not //) or #")
    outside.append(text[pos:])
    rest = "".join(outside)
    if "<" in rest:
        problems.append("a < that does not open a complete allowed tag is not allowed (write &lt;)")
    if '"' in rest:
        problems.append('a raw " is not allowed outside tags (write &quot; or use typographic quotes)')
    if re.search(r"<\s*(script|style|iframe)|javascript:", text, re.I):
        problems.append("scripts, styles and iframes are not allowed")
    return problems


def load(platform, site_dir, source="", config=None):
    """Read and validate one content folder. Raises ContentError listing every problem."""
    config = config or load_config()
    if platform not in config["platforms"]:
        raise ContentError(platform, [f"unknown platform {platform!r} (known: {', '.join(config['platforms'])})"])
    site_dir = Path(site_dir).resolve()
    label = f"{platform} ({source or site_dir})"
    path = site_dir / "content.json"
    if not path.is_file():
        raise ContentError(label, [f"{path} not found (the app repo needs site/content.json on main)"])
    try:
        doc = json.loads(read_text(path))
    except json.JSONDecodeError as e:
        raise ContentError(label, [f"site/content.json is not valid JSON: {e}"]) from None
    problems = []
    if not isinstance(doc, dict):
        raise ContentError(label, ["site/content.json must be a JSON object"])
    unknown = set(doc) - {"contract", "platform", "assets", "texts"}
    if unknown:
        problems.append(f"unknown top-level fields: {', '.join(sorted(unknown))}")
    if doc.get("contract") != CONTRACT:
        problems.append(f'"contract" must be {CONTRACT} (found {doc.get("contract")!r})')
    if doc.get("platform") != platform:
        problems.append(f'"platform" must be "{platform}" (found {doc.get("platform")!r})')

    # Screenshots: "assets" is a folder relative to site/, inside the repository (default site/assets, optional)
    assets_dir, assets = None, []
    declared = doc.get("assets")
    if declared is not None and not isinstance(declared, str):
        problems.append('"assets" must be a path (string) relative to site/')
    else:
        candidate = (site_dir / (declared or "assets")).resolve()
        repo_root = site_dir.parent
        if not candidate.is_relative_to(repo_root):
            problems.append(f'"assets" ({declared}) must stay inside the repository')
        elif candidate.is_dir():
            assets_dir = candidate
        elif declared:
            problems.append(f'"assets" folder {declared} not found')
    if assets_dir:
        for f in sorted(assets_dir.rglob("*")):
            rel = f.relative_to(assets_dir)
            if not f.is_file() or f.suffix.lower() not in ASSET_TYPES:
                continue
            if len(rel.parts) == 1 or (len(rel.parts) == 2 and rel.parts[0] in LANGS[1:]):
                assets.append(rel.as_posix())

    # Texts: the 5 languages, the same keys in each, every required key, only harmless inline HTML
    texts = doc.get("texts")
    if not isinstance(texts, dict):
        problems.append('"texts" must be an object with one entry per language (en, pt, de, es, zh)')
        texts = {}
    for lang in LANGS:
        if lang not in texts:
            problems.append(f"language {lang!r} is missing from texts")
        elif not isinstance(texts[lang], dict):
            problems.append(f"texts.{lang} must be an object of key: text")
    extra_langs = set(texts) - set(LANGS)
    if extra_langs:
        problems.append(f"unknown languages in texts: {', '.join(sorted(extra_langs))} (the site has {', '.join(LANGS)})")
    english = texts.get("en") if isinstance(texts.get("en"), dict) else {}
    missing_required = [k for k in required_keys(platform, config) if k not in english]
    if missing_required:
        problems.append(f"texts.en is missing keys the page needs: {', '.join(missing_required)}")
    for lang in LANGS:
        t = texts.get(lang)
        if not isinstance(t, dict):
            continue
        missing = sorted(set(english) - set(t))
        if missing and lang != "en":
            problems.append(f"texts.{lang} is missing keys that en has: {', '.join(missing)}")
        extra = sorted(set(t) - set(english))
        if extra and lang != "en":
            problems.append(f"texts.{lang} has keys that en does not: {', '.join(extra)}")
        for key, value in t.items():
            if not isinstance(value, str) or not value.strip():
                problems.append(f"texts.{lang}.{key} must be a non-empty string")
                continue
            for p in check_html(value):
                problems.append(f"texts.{lang}.{key}: {p}")

    # Every screenshot the pages show must exist, in English and in each language folder
    per_lang, english_only = shot_refs(platform, config)
    have = set(assets)
    for name in english_only:
        if name not in have:
            problems.append(f"asset {name} is missing (the pages link to it)")
    for name in per_lang:
        for lang in LANGS:
            rel = name if lang == "en" else f"{lang}/{name}"
            if rel not in have:
                problems.append(f"screenshot {rel} is missing (the {lang} pages show it)")

    if problems:
        raise ContentError(label, problems)
    return Content(platform, site_dir, {lang: texts[lang] for lang in LANGS}, assets_dir, assets, source)


def main(argv):
    if len(argv) != 3 or argv[1] in ("-h", "--help"):
        print(__doc__.strip())
        return 2
    try:
        c = load(argv[1], argv[2], source=f"local {argv[2]}")
    except ContentError as e:
        print(e, file=sys.stderr)
        if os.environ.get("GITHUB_ACTIONS"):
            print("::error title=Site content::" + str(e).replace("%", "%25").replace("\n", "%0A"))
        return 1
    print(f"ok: {c.platform} content is valid ({len(LANGS)} languages, {len(c.texts['en'])} texts, {len(c.assets)} asset files)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
