#!/usr/bin/env python3
"""Builds https://menew.pururucos.com.br into public/ (never committed; CI builds and deploys it).

    python3 scripts/build.py                                  # content from each app repo's main, releases from GitHub
    python3 scripts/build.py --serve                          # same, then preview at http://localhost:8000
    python3 scripts/build.py --content mac=../menew/site      # use a local content folder for one platform (repeatable)
    python3 scripts/build.py --content windows=fallback       # force the Windows "coming soon" fallback content
    python3 scripts/build.py --offline --content mac=... --content linux=...   # no network (versions show as "dev")

The same overrides work as environment variables: MENEW_CONTENT_MAC, MENEW_CONTENT_LINUX, MENEW_CONTENT_WINDOWS.

What comes from where:
  - layout (site/templates), the overview page and the /vs pages, shared strings (site/i18n), the Pururucos band
    (site/family.json + static/assets/family), SEO files and static files (static/): this repository;
  - the /mac, /linux and /windows texts and screenshots: site/ in each app repository (scripts/content.py, the contract);
  - versions, download links and whether Windows is released: the GitHub Releases API, at build time.
Python standard library only. Edit the templates and texts, never the generated files.
"""
import argparse
import datetime
import functools
import html as htmllib
import http.server
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import content as contract
import sources
from content import LANGS as LANG_CODES, ContentError, read_text

ROOT = contract.ROOT
SITE = "https://menew.pururucos.com.br"
INDEXNOW_KEY = "df850856e8360dcdd128c85eb1c159fd"
LANGS = {  # code: (lang attribute, native name)
    "en": ("en", "English"),
    "pt": ("pt-BR", "Português"),
    "de": ("de", "Deutsch"),
    "es": ("es", "Español"),
    "zh": ("zh-Hans", "中文"),
}
assert tuple(LANGS) == LANG_CODES
OG_LOCALE = {"en": "en_US", "pt": "pt_BR", "de": "de_DE", "es": "es_ES", "zh": "zh_CN"}

# Plain facts for llms.txt: what each part does, so an AI does not have to guess (it used to say "no thirds", "no temperatures")
FACTS = [
    ("Window manager", "Snap windows to halves, quarters (corners), thirds and two-thirds, maximize, center and restore, with the same ⌃⌥ shortcuts as Rectangle; drag a window to a screen edge or corner to snap it; move it to the next display; a layout grid in Actions; work profiles bring apps and window layouts back."),
    ("Command palette (⌃⌥Space)", "Apps, Menew actions, window layouts, snippets, notes and clipboard items; math, unit, currency and time zone conversions; text transforms (case, JSON, Base64, word count); developer tools (ports in use, UUID, Unix timestamps, JWT); quick links and web search; emoji and symbols; timers, alarms and reminders; your own script commands in shell, Python, Node or AppleScript (Raycast-compatible); natural-language commands with on-device AI."),
    ("Clipboard history (⌘⇧V)", "Text, links, images and files with search (including text inside images, read on the Mac, and search by meaning), pins, filters and previews; snippets with abbreviations that expand as you type, variables, fields and cursor position; a paste queue; items marked by password managers are skipped; a Passwords tab pastes passwords notes after Touch ID."),
    ("System monitor", "CPU and GPU with live graphs, memory pressure and swap, disk space and read/write speed, network usage per app, battery health, cycles and wear over time, temperatures of the chip, SSD and battery, fan speed, performance and efficiency cores, power in watts, SSD health, 24-hour, 7-day and 30-day history, custom alert rules, a plain-language 'Why is my Mac slow?' diagnosis and screen time."),
    ("Screenshots and recording", "Capture an area, window or the whole screen; annotate with arrows, text and blur; pin on screen; record the screen with sound, as video or GIF; scrolling capture; copy text from anywhere on screen (OCR on the Mac)."),
    ("Meetings and recordings", "Next meeting with a Join button for Zoom, Meet, Teams and Webex; notch alerts that skip calendar blocks; record Mac audio and the microphone as separate tracks; on-device transcription; minutes with the main topic, a summary, each topic discussed, decisions, a task table with owner and deadline, and next steps; tasks to Reminders."),
    ("Memory", "⌃⌥⌘K saves what is on screen with its text, app and link; ask in plain words and get an answer with sources; a knowledge graph of topics; items appear in Spotlight."),
    ("Files and cleanup", "A drag-and-drop shelf with image and PDF tools (convert, compress, split, OCR, password-protect); organize a folder by file content with review and undo; disk cleaner for caches, logs, large and duplicate files (files go to the Trash); app uninstaller with leftovers; login and background items."),
    ("AI coding agents", "Claude Code, Codex, Gemini CLI, Cursor, Copilot CLI, Kiro, OpenCode, Droid, Qwen Code, Amp, OpenClaw, Kimi, Goose and Pi sessions with their state (working, done, waiting for a permission or answer) through each tool's hooks (or its running process and logs when it has none), shown in the notch with a button that opens the right terminal; plan limits (5-hour and weekly windows) with reset times, a forecast at the current pace and alerts at 75% and 90%; tokens per day, estimated cost at Anthropic API prices, cache savings, top models and projects, read from the tools' local logs; Ollama and LM Studio models loaded in memory; a local MCP server (Menew --mcp) for Claude Code, Codex, Gemini CLI, Claude Desktop, Cursor, Kiro, Copilot CLI, OpenCode, Qwen Code, Droid, Amp and Kimi to search Memory, notes and meeting minutes, check the Mac and save notes."),
    ("Made for macOS", "Live notch alerts and a hover panel with call controls; desktop widgets; Siri, Shortcuts and Spotlight actions; Focus filters; Live Text in images; Continuity Camera; live translated captions; Liquid Glass design on macOS 26; English, Portuguese, German, Spanish and Chinese."),
]
NOT_INCLUDED = ("Menew does not hide or rearrange other apps' menu bar icons (use Ice or Thaw), has no extension store "
                "(it runs your own scripts), does not sync to a cloud, and does not embed a web browser in the menu bar.")

# Website visit counter (GoatCounter: no cookies, no personal data). Empty = off. The apps themselves have no telemetry.
GOATCOUNTER = "menew"
ANALYTICS = (f'<script data-goatcounter="https://{GOATCOUNTER}.goatcounter.com/count" async src="//gc.zgo.at/count.js"></script>'
             if GOATCOUNTER else "")

# Only the overview redirects, and only visitors who have not picked a language in the menu yet
DETECT = """<script>
  (function () {
    try {
      var supported = ["pt", "de", "es", "zh"];
      var pick = localStorage.getItem("menew-lang") || (navigator.languages || [navigator.language])
        .map(function (l) { return l.slice(0, 2).toLowerCase(); })
        .filter(function (l) { return l === "en" || supported.indexOf(l) >= 0; })[0];
      if (pick && supported.indexOf(pick) >= 0) location.replace("/" + pick + location.hash);
    } catch (e) {}
  })();
</script>"""

# "X alternative" the way people search for it in each language
ALT = {"en": "alternative", "pt": "alternativa", "de": "Alternative", "es": "alternativa", "zh": "替代"}
HERO = ("popover-now-dark",)  # above the fold: load right away; every other screenshot loads lazily


# ---------------------------------------------------------------- small helpers

def plain(text):
    """Text without HTML, for structured data"""
    return htmllib.unescape(re.sub(r"<[^>]+>", "", text))


def fold(text, more, tag="p"):
    """First sentence visible and the rest in a <details>: the page stays light and the whole text stays in the HTML
    (search engines and AIs read collapsed content)."""
    best = None
    for m in re.finditer(r"([.!?])\s+(?=[^\s<])|。", text):
        before = text[:m.end()]
        if before.count("<") != before.count(">"):
            continue
        # Chinese says much more with fewer characters
        if len(plain(before)) >= (24 if re.search("[一-鿿]", text) else 60):
            best = m
            break
    if not best or len(plain(text[best.end():])) < 40:
        return f"<{tag}>{text}</{tag}>"
    lead, rest = text[:best.end()].rstrip(), text[best.end():].lstrip()
    items, start = [], 0
    for m in re.finditer(r"(?<=[.!?])\s+(?=[^\s<])|(?<=。)", rest):
        chunk = rest[start:m.start()].strip()
        if chunk and chunk.count("<") == chunk.count(">"):
            items.append(chunk)
            start = m.end()
    if rest[start:].strip():
        items.append(rest[start:].strip())
    bullets = "".join(f"<li>{i}</li>" for i in items)
    return f'<{tag}>{lead}</{tag}><details class="more"><summary>{more}</summary><ul>{bullets}</ul></details>'


def url(lang):
    return SITE + "/" if lang == "en" else f"{SITE}/{lang}"


def path(lang, page=""):
    """Local path of a page in a language ('' = overview, 'mac', 'linux', 'windows')"""
    if lang == "en":
        return f"/{page}" if page else "/"
    return f"/{lang}/{page}" if page else f"/{lang}"


def page_link(lang, page):
    return SITE + path(lang, page)


def page_hreflang(page):
    links = "\n".join(f'<link rel="alternate" hreflang="{LANGS[c][0]}" href="{page_link(c, page)}">' for c in LANGS)
    return links + f'\n<link rel="alternate" hreflang="x-default" href="{page_link("en", page)}">'


def lang_paths(page=""):
    """Address of each language ('' = overview, 'linux' = the Linux page)"""
    return "{" + ",".join(f"{c}:'" + (f"/{page}" if c == "en" else f"/{c}" + (f"/{page}" if page else "")) + "'" for c in LANGS) + "}"


def switch(current, paths=None):
    options = "".join(
        f'<option value="{code}"{" selected" if code == current else ""}>{name}</option>'
        for code, (_, name) in LANGS.items())
    return ('<select class="lang" aria-label="Language" onchange="'
            "try{localStorage.setItem('menew-lang',this.value)}catch(e){}"
            "try{goatcounter.count({path:'lang-'+this.value,event:true})}catch(e){}"
            # Fixed addresses: the <select> value never becomes a URL directly (CodeQL alert)
            "var u=" + (paths or lang_paths()) + "[this.value];if(u)location.href=u\">" + options + "</select>")


def ld_json(data):
    return json.dumps(data, ensure_ascii=False).replace("</", "<\\/")


def render(tpl, values, flags=None, label="page"):
    """{{key}} placeholders, plus {{#flag}}…{{/flag}} (shown when true) and {{^flag}}…{{/flag}} (shown when false).
    A section tag that ends its line takes the line break with it, so templates stay readable."""
    flags = flags or {}
    tpl = re.sub(r"(?m)^(\{\{/\w+\}\})\n", r"\1", tpl)
    tpl = re.sub(r"(\{\{[#^]\w+\}\})\n", r"\1", tpl)

    def section(m):
        if m.group(2) not in flags:
            raise SystemExit(f"{label}: unknown section flag {m.group(2)!r}")
        return m.group(3) if bool(flags[m.group(2)]) == (m.group(1) == "#") else ""

    prev = None
    while prev != tpl:
        prev = tpl
        tpl = re.sub(r"\{\{([#^])(\w+)\}\}(.*?)\{\{/\2\}\}", section, tpl, flags=re.S)
    missing = contract.placeholders(tpl) - set(values)
    if missing:
        raise SystemExit(f"{label}: no value for {', '.join(sorted(missing))}")
    return re.sub(r"\{\{(\w+)\}\}", lambda m: values[m.group(1)], tpl)


def write(out, rel, text):
    dest = out / rel.lstrip("/")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text, encoding="utf-8", newline="\n")
    return dest


# ---------------------------------------------------------------- the build

class Build:
    def __init__(self, out, overrides, offline):
        self.out = out
        self.config = contract.load_config()
        self.offline = offline
        self.family = json.loads(read_text(ROOT / "site/family.json"))
        self.maker = self.family["maker"]
        self.org = {"@type": "Organization", "@id": self.maker["id"], "name": self.maker["name"], "url": self.maker["url"]}
        self.shared = {lang: json.loads(read_text(ROOT / f"site/i18n/{lang}.json")) for lang in LANGS}
        missing = {lang: sorted(set(self.shared["en"]) - set(t)) for lang, t in self.shared.items()}
        for lang, keys in missing.items():
            if keys:
                raise SystemExit(f"site/i18n/{lang}.json is missing {keys}")
        self.compare = json.loads(read_text(ROOT / "site/compare.json"))
        self.template = read_text(ROOT / "site/templates/overview.html")
        self.style = re.search(r"<style>\n(.*?)</style>", self.template, re.S).group(1)
        self.load_sources(overrides)

    # -- content and releases ------------------------------------------------------------------

    def load_sources(self, overrides):
        self.content, self.states, problems = {}, {}, []
        tmp = None
        for platform, cfg in self.config["platforms"].items():
            where = overrides.get(platform)
            try:
                if where == "fallback" or (where is None and self.offline and cfg.get("fallback")):
                    if not cfg.get("fallback"):
                        raise SystemExit(f"{platform} has no fallback content")
                    state = {"repo": cfg["repo"], "content": "fallback", "commits": [],
                             "release": None if self.offline else sources.pick_release(cfg["repo"], cfg["release"])}
                    site_dir, label = ROOT / cfg["fallback"], "fallback"
                elif where:
                    state = sources.local_state(platform, cfg, where, self.offline)
                    site_dir, label = Path(where), f"local {where}"
                elif self.offline:
                    raise SystemExit(f"--offline needs --content {platform}=PATH")
                else:
                    state = sources.remote_state(platform, cfg)
                    if state["content"] == "fallback":
                        site_dir, label = ROOT / cfg["fallback"], f"fallback ({cfg['repo']} has no site/content.json)"
                    else:
                        tmp = tmp or sources.workdir()
                        site_dir = sources.clone(cfg["repo"], tmp)
                        label = f"{cfg['repo']}@main"
                self.content[platform] = contract.load(platform, site_dir, label, self.config)
                self.states[platform] = state
                print(f"{platform}: {label} ({len(self.content[platform].assets)} asset files)"
                      + (f", release {state['release']['tag']}" if state.get("release") else ", no release"))
            except ContentError as e:
                problems.append(str(e))
        if problems:
            raise SystemExit("\n\n".join(problems))
        self.site_commit = sources.site_commit()
        self.fingerprint = sources.fingerprint(self.site_commit, self.states)

        mac, linux, win = (self.states[p]["release"] for p in ("mac", "linux", "windows"))
        self.version = mac["tag"].lstrip("v") if mac else "dev"
        self.linux_rel = linux
        self.linux_pre = linux["prerelease"] if linux else True
        self.win_rel = win
        self.win_released = win is not None
        # Date of the last change to the site: this repo's site files, each app's content and each release
        dirty = subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain", "--", "site", "static", "platforms.json"],
                               capture_output=True, text=True).stdout.strip()
        own = datetime.date.today().isoformat() if dirty else (
            subprocess.run(["git", "-C", str(ROOT), "log", "-1", "--format=%cs", "--", "site", "static", "platforms.json"],
                           capture_output=True, text=True).stdout.strip() or datetime.date.today().isoformat())
        dates = [own] + [c["date"] for s in self.states.values() for c in s.get("commits", [])] \
                      + [s["release"]["published"] for s in self.states.values() if s.get("release") and s["release"]["published"]]
        self.updated = max(dates)

    def texts(self, platform, lang):
        return self.content[platform].texts[lang]

    def page_values(self, platform, lang):
        """Values for a platform page: the shared strings it may use, then its own content (which wins)."""
        cfg = self.config["platforms"][platform]
        values = {k: self.shared[lang][k] for k in cfg["shared"]}
        values.update(self.texts(platform, lang))
        return values

    def win_badge(self, lang):
        t = self.shared[lang]
        if not self.win_released:
            return t["plat_win_badge"]
        return t["plat_linux_badge"] if self.win_rel["prerelease"] else t["plat_mac_badge"]

    # -- files ---------------------------------------------------------------------------------

    def copy_files(self):
        if self.out.exists():
            shutil.rmtree(self.out)
        shutil.copytree(ROOT / "static", self.out)
        for platform, c in self.content.items():
            if not c.assets_dir:
                continue
            base = self.out / self.config["platforms"][platform]["assets_url"].lstrip("/")
            for rel in c.assets:
                dest = base / rel
                if dest.exists():
                    raise SystemExit(f"{platform}: asset {rel} collides with {dest.relative_to(self.out)} (rename it)")
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(c.assets_dir / rel, dest)

    def optimize_images(self, page):
        """WebP when the app repo ships one next to the PNG (about 1/3 of the size), and lazy loading below the fold"""
        def fix(m):
            tag, src = m.group(0), m.group(1)
            if (self.out / src.lstrip("/")).with_suffix(".webp").exists():
                tag = tag.replace(f'src="{src}"', f'src="{src[:-4]}.webp"')
            if 'class="shot' in tag and not any(h in src for h in HERO):
                tag = tag.replace("<img ", '<img loading="lazy" decoding="async" ', 1)
            return tag
        return re.sub(r'<img [^>]*src="(/assets/[^"]+\.png)"[^>]*>', fix, page)

    # -- Pururucos band ------------------------------------------------------------------------

    def utm(self, link, medium):
        if link.count("/") == 2:  # https://site.app -> https://site.app/
            link += "/"
        return link + ("&" if "?" in link else "?") + f"utm_source=menew&utm_medium={medium}"

    def maker_url(self, lang, medium):
        """Pururucos site: the root is Portuguese; the other languages go to /en/"""
        return self.utm(self.maker["url"].rstrip("/") + ("/" if lang == "pt" else "/en/"), medium)

    def family_img(self, remote):
        local = "/assets/family/" + remote.rsplit("/", 1)[-1]
        if not (ROOT / "static" / local.lstrip("/")).exists():
            raise SystemExit(f"static{local} is missing: run python3 scripts/sync-family.py")
        return local

    def family_band(self, lang):
        esc = htmllib.escape
        card = ('      <li><a class="family-card{cls}" href="{href}" data-goatcounter-click="{click}" target="_blank" rel="noopener">'
                '<img src="{img}" width="44" height="44" alt="" loading="lazy" decoding="async">'
                '<span><b>{name}</b><span>{text}</span></span></a></li>')
        cards = [card.format(cls="", href=esc(self.utm(p["url"], "apps_band")), click=f"family-{p['id']}",
                             img=self.family_img(p["icon"]), name=esc(p["name"]), text=esc(p["tagline"][lang]))
                 for p in self.family["products"] if p["id"] != "menew"]
        idea = self.maker["yourIdea"]
        cards.append(card.format(cls=" idea", href=esc(self.maker_url(lang, "apps_band")), click="family-your-idea",
                                 img=self.family_img(self.maker["logo"]), name=esc(idea["title"][lang]), text=esc(idea["body"][lang])))
        title = esc(self.maker["othersTitle"][lang])
        return (f'<aside class="family" aria-labelledby="family-title">\n  <div class="wrap">\n'
                f'    <h2 id="family-title">{title}</h2>\n    <ul class="family-grid">\n' + "\n".join(cards)
                + '\n    </ul>\n  </div>\n</aside>')

    def family_credit(self, lang):
        esc = htmllib.escape
        return (f'<a class="maker" href="{esc(self.maker_url(lang, "footer"))}" data-goatcounter-click="footer-pururucos">'
                f'<img src="{self.family_img(self.maker["logo"])}" width="16" height="16" alt="">{esc(self.maker["credit"][lang])}</a>'
                f' · <a href="{esc(self.maker_url(lang, "footer"))}" data-goatcounter-click="footer-pururucos-cta">{esc(self.maker["cta"][lang])}</a>')

    # -- overview (/, /pt, /de, /es, /zh) ------------------------------------------------------

    def overview_jsonld(self, lang, texts, n_faq, n_rep):
        shots = "/assets" if lang == "en" else f"/assets/{lang}"
        app = {
            "@type": "SoftwareApplication",
            "name": "Menew",
            # The name alone is also a restaurant system (Linx Menew): these make clear which is which
            "alternateName": ["Menew for Mac", "Menew Mac app", "Menew menu bar app", "Quost Menew"],
            "disambiguatingDescription": "Free, open source macOS menu bar app by Pururucos (pururucos.com.br; source at github.com/Quost/menew). Not related to Linx Menew, a restaurant point-of-sale system.",
            "url": url(lang),
            "description": htmllib.unescape(texts["meta_desc"]),
            "inLanguage": LANGS[lang][0],
            "operatingSystem": "macOS 14 or later",
            "applicationCategory": "UtilitiesApplication",
            "offers": {"@type": "Offer", "price": "0", "priceCurrency": "USD"},
            "downloadUrl": "https://github.com/Quost/menew/releases/latest/download/Menew.dmg",
            "image": f"{SITE}/assets/og/{lang}.png",
            "screenshot": [f"{SITE}{shots}/{name}-dark.png" for name in self.config["platforms"]["mac"]["screenshots"]],
            "softwareVersion": self.version,
            "dateModified": self.updated,
            "featureList": [plain(texts[f"b{i}_t"]) + ": " + plain(texts[f"b{i}_d"]) for i in range(1, 6)]
                           + [plain(texts[f"f{i}_t"]) + ": " + plain(texts[f"f{i}_d"]) for i in range(1, 7)]
                           + [plain(texts[f"act_l{i}"]) for i in range(1, 11)]
                           + [plain(texts[f"nat{i}_t"]) + ": " + plain(texts[f"nat{i}_d"]) for i in range(1, 8)]
                           + [plain(texts[f"rep{i}_d"]) for i in range(1, n_rep + 1)],
            "license": "https://www.gnu.org/licenses/gpl-3.0.html",
            "isAccessibleForFree": True,
            "keywords": ", ".join(plain(texts[k]) for k in ("b1_t", "b2_t", "b3_t", "b4_t", "f2_t", "f1_t", "f3_t"))
                        + ", " + ", ".join(f"{n} {ALT[lang]}" for i in range(1, n_rep + 1) for n in texts[f"rep{i}_t"].split(" · ")),
            "author": self.org,
            "publisher": self.org,
            "sameAs": ["https://github.com/Quost/menew"],
        }
        faq = {
            "@type": "FAQPage",
            "mainEntity": [{"@type": "Question", "name": plain(texts[f"faq{i}_q"]),
                            "acceptedAnswer": {"@type": "Answer", "text": plain(texts[f"faq{i}_a"])}} for i in range(1, n_faq + 1)],
        }
        site = {"@type": "WebSite", "name": "Menew", "alternateName": "Menew for Mac", "url": url(lang), "inLanguage": LANGS[lang][0],
                "description": htmllib.unescape(texts["og_desc"])}
        return ld_json({"@context": "https://schema.org", "@graph": [site, app, faq]})

    def compare_links(self, label):
        links = " · ".join(f'<a href="/vs/{slug}" data-goatcounter-click="vs-{slug}">{htmllib.escape(c["other"])}</a>'
                           for slug, c in self.compare.items())
        return f'<p class="rep-note">{label} {links}</p>'

    def build_overview(self):
        english = self.shared["en"]
        n_faq = sum(1 for k in english if re.fullmatch(r"faq\d+_q", k))
        n_rep = sum(1 for k in english if re.fullmatch(r"rep\d+_t", k))
        hreflang = "\n".join(f'<link rel="alternate" hreflang="{LANGS[c][0]}" href="{url(c)}">' for c in LANGS)
        hreflang += f'\n<link rel="alternate" hreflang="x-default" href="{url("en")}">'
        for lang in LANGS:
            texts = self.shared[lang]
            win = self.texts("windows", lang)
            values = dict(texts)
            values.update({
                "html_lang": LANGS[lang][0],
                "url": url(lang),
                "hreflang": hreflang,
                "detect": DETECT if lang == "en" else "",
                "shots": "/assets" if lang == "en" else f"/assets/{lang}",
                "og_image": f"{SITE}/assets/og/{lang}.png",
                "og_locale": OG_LOCALE[lang],
                "lang_switch": switch(lang),
                "linux_path": path(lang, "linux"), "mac_path": path(lang, "mac"), "windows_path": path(lang, "windows"),
                "jsonld": self.overview_jsonld(lang, texts, n_faq, n_rep),
                **{f"f{i}_d_fold": fold(texts[f"f{i}_d"], texts["more"]) for i in range(1, 7)},
                **{f"act_l{i}_fold": "<div>" + fold(texts[f"act_l{i}"], texts["more"], "span") + "</div>" for i in range(1, 11)},
                "faq_items": "\n".join(f'      <details class="reveal"><summary>{texts[f"faq{i}_q"]}</summary><p>{texts[f"faq{i}_a"]}</p></details>'
                                       for i in range(1, n_faq + 1)),
                "compare_links": self.compare_links(texts["compare_label"]),
                "family_band": self.family_band(lang), "family_credit": self.family_credit(lang),
                "analytics": ANALYTICS,
                # Platform cards: Linux and Windows labels follow their releases (prerelease = preview)
                "linux_tag": "preview" if self.linux_pre else "stable",
                "linux_badge": texts["plat_linux_badge"] if self.linux_pre else texts["plat_mac_badge"],
                "win_tag": ("preview" if self.win_rel["prerelease"] else "stable") if self.win_released else "soon",
                "win_badge": self.win_badge(lang),
                "win_card_d": win["win_card_d"] if self.win_released else texts["plat_win_d"],
                "win_card_cta": plain(win["win_h1"]) if self.win_released else texts["plat_win_cta"],
                "js_strings": json.dumps({**{k: texts[k] for k in ("version", "notch_meeting", "notch_join", "notch_focus")},
                                          **{k: htmllib.unescape(texts[k]) for k in ("platforms", "plat_linux_cta")},
                                          "win_name": plain(win["win_h1"]),
                                          "linux_path": path(lang, "linux"), "windows_path": path(lang, "windows")}, ensure_ascii=False),
            })
            page = self.optimize_images(render(self.template, values, label=f"overview {lang}"))
            write(self.out, "index.html" if lang == "en" else f"{lang}/index.html", page)
        print("overview: 5 languages")

    # -- comparisons (/vs, English only) -------------------------------------------------------

    def build_compare(self):
        ctpl = read_text(ROOT / "site/templates/compare.html")
        family_style = re.search(r"  /\* Pururucos: início.*?/\* Pururucos: fim \*/\n", self.template, re.S).group(0)
        ctpl = ctpl.replace("{{family_style}}", family_style.rstrip("\n"))
        mark = {"yes": ("✓ Yes", "yes"), "partial": ("◐ Partly", "partial"), "no": ("— No", "no")}
        esc = htmllib.escape
        for slug, c in self.compare.items():
            rows = []
            for feature, menew, other, note in c["rows"]:
                n = f'<span class="note">{esc(note)}</span>' if note else ""
                rows.append(f'      <tr><td>{esc(feature)}{n}</td>'
                            f'<td class="mark {mark[menew][1]}">{mark[menew][0]}</td><td class="mark {mark[other][1]}">{mark[other][0]}</td></tr>')
            page_url = f"{SITE}/vs/{slug}"
            ld = {"@context": "https://schema.org", "@graph": [
                {"@type": "WebPage", "name": c["title"], "url": page_url, "description": c["description"], "inLanguage": "en",
                 "about": {"@type": "SoftwareApplication", "name": "Menew", "alternateName": "Menew for Mac", "operatingSystem": "macOS",
                           "url": SITE + "/mac", "offers": {"@type": "Offer", "price": "0", "priceCurrency": "USD"},
                           "author": self.org, "publisher": self.org},
                 "dateModified": self.updated},
                {"@type": "BreadcrumbList", "itemListElement": [
                    {"@type": "ListItem", "position": 1, "name": "Menew", "item": SITE + "/"},
                    {"@type": "ListItem", "position": 2, "name": "Comparisons", "item": f"{SITE}/vs"},
                    {"@type": "ListItem", "position": 3, "name": f"Menew vs {c['other']}", "item": page_url}]},
            ]}
            values = {
                "title": esc(c["title"]), "description": esc(c["description"]), "url": page_url, "slug": slug,
                "other": esc(c["other"]), "short": esc(c["other"]), "intro": esc(c["intro"]),
                "rows": "\n".join(rows),
                "other_better": "".join(f"<li>{esc(x)}</li>" for x in c["other_better"]),
                "menew_better": "".join(f"<li>{esc(x)}</li>" for x in c["menew_better"]),
                "others": " ".join(f'<a href="/vs/{s2}">{esc(c2["other"])}</a>' for s2, c2 in self.compare.items() if s2 != slug),
                "jsonld": ld_json(ld),
                "analytics": ANALYTICS,
                "family_band": self.family_band("en"), "family_credit": self.family_credit("en"),
            }
            write(self.out, f"vs/{slug}/index.html", render(ctpl, values, label=f"vs/{slug}"))
        # Index of the comparisons, same look
        head = ctpl.split("<main", 1)[0]
        cards = "".join(f'<div class="box" style="margin-bottom:14px"><h3><a href="/vs/{slug}">Menew vs {esc(c["other"])}</a></h3>'
                        f'<p style="color:var(--muted);font-size:15px">{esc(c["description"])}</p></div>' for slug, c in self.compare.items())
        index_title = "Menew compared: free, open source alternatives for Mac utilities"
        index_desc = "How Menew, a free and open source Mac menu bar app, compares with Raycast, Maccy, Rectangle, iStat Menus, CleanShot X and Otter."
        index_ld = {"@context": "https://schema.org", "@graph": [
            {"@type": "CollectionPage", "name": index_title, "url": f"{SITE}/vs", "description": index_desc, "inLanguage": "en",
             "publisher": self.org, "dateModified": self.updated,
             "mainEntity": {"@type": "ItemList", "itemListElement": [
                 {"@type": "ListItem", "position": i, "name": c["title"], "url": f"{SITE}/vs/{slug}"}
                 for i, (slug, c) in enumerate(self.compare.items(), 1)]}},
            {"@type": "BreadcrumbList", "itemListElement": [
                {"@type": "ListItem", "position": 1, "name": "Menew", "item": SITE + "/"},
                {"@type": "ListItem", "position": 2, "name": "Comparisons", "item": f"{SITE}/vs"}]},
        ]}
        index_values = {"title": index_title, "description": index_desc,
                        "url": f"{SITE}/vs", "short": "All", "jsonld": ld_json(index_ld), "analytics": ANALYTICS}
        index = re.sub(r"\{\{(\w+)\}\}", lambda m: index_values.get(m.group(1), ""), head)
        index += ('<main class="wrap"><header><div class="eyebrow">Comparisons</div><h1>Menew compared</h1>'
                  '<p class="intro">Menew is one free, open source app for the Mac menu bar that covers the everyday part of several utilities. '
                  'Here is how it compares, including where the specialized apps go further.</p></header><div style="margin-top:28px">'
                  + cards + '</div><p class="disclaimer">Names are trademarks of their owners and appear only for comparison.</p></main>'
                  + self.family_band("en") + '\n<footer><div class="wrap">' + self.family_credit("en") + '<br>Menew · <a href="/">menew.pururucos.com.br</a> · '
                  '<a href="https://github.com/Quost/menew" data-goatcounter-click="footer-source">Source on GitHub</a> · Not related to Linx Menew.</div></footer></body></html>\n')
        write(self.out, "vs/index.html", index)
        print(f"vs: {len(self.compare)} comparisons")

    # -- platform pages (/mac, /linux, /windows) -----------------------------------------------

    def platform_page(self, platform, lang, ld, extra, flags=None):
        cfg = self.config["platforms"][platform]
        page = cfg["page"]
        values = self.page_values(platform, lang)
        values.update({
            "html_lang": LANGS[lang][0], "url": page_link(lang, page), "hreflang": page_hreflang(page),
            "home": path(lang), "mac_path": path(lang, "mac"), "linux_path": path(lang, "linux"), "windows_path": path(lang, "windows"),
            "shots": cfg["assets_url"] if lang == "en" else f"{cfg['assets_url']}/{lang}",
            "og_image": f"{SITE}/assets/og/{lang}.png", "og_locale": OG_LOCALE[lang],
            "lang_switch": switch(lang, lang_paths(page)), "style": self.style, "analytics": ANALYTICS,
            "jsonld": ld_json(ld),
            "family_band": self.family_band(lang), "family_credit": self.family_credit(lang),
            "win_badge": self.win_badge(lang),
        })
        values.update(extra)
        tpl = read_text(ROOT / "site/templates" / cfg["template"])
        out_page = self.optimize_images(render(tpl, values, flags, label=f"{page} {lang}"))
        write(self.out, path(lang, page).lstrip("/") + "/index.html", out_page)

    def build_mac(self):
        for lang in LANGS:
            tx = self.page_values("mac", lang)
            mac_url = page_link(lang, "mac")
            ld = {"@context": "https://schema.org", "@graph": [
                {"@type": "WebPage", "name": plain(tx["mac_title"]), "url": mac_url, "description": plain(tx["mac_meta_desc"]),
                 "inLanguage": LANGS[lang][0], "dateModified": self.updated, "isPartOf": {"@type": "WebSite", "name": "Menew", "url": url(lang)},
                 "about": {"@type": "SoftwareApplication", "name": "Menew", "alternateName": "Menew for Mac", "url": mac_url,
                           "operatingSystem": "macOS 14 or later", "applicationCategory": "UtilitiesApplication",
                           "downloadUrl": "https://github.com/Quost/menew/releases/latest/download/Menew.dmg", "softwareVersion": self.version,
                           "offers": {"@type": "Offer", "price": "0", "priceCurrency": "USD"}, "isAccessibleForFree": True,
                           "license": "https://www.gnu.org/licenses/gpl-3.0.html", "author": self.org, "publisher": self.org}},
                {"@type": "BreadcrumbList", "itemListElement": [
                    {"@type": "ListItem", "position": 1, "name": "Menew", "item": url(lang)},
                    {"@type": "ListItem", "position": 2, "name": "Mac", "item": mac_url}]},
            ]}
            self.platform_page("mac", lang, ld, {"js_strings": json.dumps(
                {"copied": tx["copied"], "version": tx["version"], "ai_prompt": tx["ai_prompt"],
                 "platforms": htmllib.unescape(tx["platforms"])}, ensure_ascii=False)})
        print("mac: 5 languages")

    def build_linux(self):
        repo_url = "https://github.com/" + self.config["platforms"]["linux"]["repo"]
        rel = self.linux_rel
        cfg = self.config["platforms"]["linux"]

        # the .deb and the AppImage of the release, linked directly (the page's script refreshes them from the API);
        # before a release has them, the links go to the releases page
        files = (rel or {}).get("files") or {}
        def pick(suffix, fallback):
            name = next((n for n in sorted(files) if n.endswith(suffix)), None)
            return (files[name], name) if name else ((rel or {}).get("url") or f"{repo_url}/releases", fallback)
        version = rel["tag"].lstrip("v") if rel else "VERSION"
        deb_url, deb_name = pick("_amd64.deb", f"menew_{version}_amd64.deb")
        appimage_url, appimage_name = pick("-x86_64.AppImage", f"Menew-{version}-x86_64.AppImage")
        linux_files = {"deb_url": deb_url, "deb_name": deb_name, "appimage_url": appimage_url, "appimage_name": appimage_name}

        def cards(t, prefix):
            n = sum(1 for k in t if re.fullmatch(rf"{prefix}\d+_t", k))
            return "\n".join(f'      <div class="feature reveal"><h3>{t[f"{prefix}{i}_t"]}</h3><p>{t[f"{prefix}{i}_d"]}</p></div>'
                             for i in range(1, n + 1))

        for lang in LANGS:
            t = self.texts("linux", lang)
            shots = cfg["assets_url"] if lang == "en" else f"{cfg['assets_url']}/{lang}"
            page_url = page_link(lang, "linux")
            feats = [plain(t[f"{p}{i}_t"]) + ": " + plain(t[f"{p}{i}_d"])
                     for p in ("pc", "cc", "tl") for i in range(1, sum(1 for k in t if re.fullmatch(rf"{p}\d+_t", k)) + 1)]
            ld = {"@context": "https://schema.org", "@graph": [
                {"@type": "WebPage", "name": plain(t["title"]), "url": page_url, "description": htmllib.unescape(t["meta_desc"]),
                 "inLanguage": LANGS[lang][0], "dateModified": self.updated,
                 "isPartOf": {"@type": "WebSite", "name": "Menew", "url": url(lang)}},
                {"@type": "SoftwareApplication", "name": "Menew for Linux", "alternateName": ["Menew Linux", "Quost Menew for Linux"],
                 "disambiguatingDescription": "Free, open source Linux port of the Menew menu bar app by Pururucos (pururucos.com.br). "
                                              + ("Early preview releases for testers. " if self.linux_pre else "")
                                              + "Not related to Linx Menew, a restaurant point-of-sale system.",
                 "url": page_url, "description": htmllib.unescape(t["meta_desc"]), "inLanguage": LANGS[lang][0],
                 "operatingSystem": "Linux", "applicationCategory": "UtilitiesApplication",
                 "offers": {"@type": "Offer", "price": "0", "priceCurrency": "USD"},
                 "image": f"{SITE}/assets/og/{lang}.png",
                 "screenshot": [f"{SITE}{shots}/{n}-dark.png" for n in cfg["screenshots"]],
                 "featureList": feats, "license": "https://www.gnu.org/licenses/gpl-3.0.html", "isAccessibleForFree": True,
                 "author": self.org, "publisher": self.org,
                 "sameAs": [repo_url], "isBasedOn": {"@type": "SoftwareApplication", "name": "Menew", "operatingSystem": "macOS", "url": url(lang)}},
                {"@type": "BreadcrumbList", "itemListElement": [
                    {"@type": "ListItem", "position": 1, "name": "Menew", "item": url(lang)},
                    {"@type": "ListItem", "position": 2, "name": "Linux", "item": page_url}]},
            ]}
            if rel:
                ld["@graph"][1]["softwareVersion"] = rel["tag"].lstrip("v")
                if files:
                    ld["@graph"][1]["downloadUrl"] = deb_url
            self.platform_page("linux", lang, ld, {
                "release_url": rel["url"] if rel else f"{repo_url}/releases",
                "release_version": rel["tag"] if rel else "",
                **linux_files,
                "badge_label": t["badge_pre"] if self.linux_pre else t["badge_stable"],
                "palette_cards": cards(t, "pc"), "clipboard_cards": cards(t, "cc"), "tools_cards": cards(t, "tl"),
                "next_cards": cards(t, "n"),
            })
        print("linux: 5 languages" + (f" ({rel['tag']}, {'preview' if self.linux_pre else 'stable'})" if rel else " (no release)"))

    def build_windows(self):
        rel = self.win_rel
        repo_url = "https://github.com/" + self.config["platforms"]["windows"]["repo"]
        for lang in LANGS:
            t = self.page_values("windows", lang)
            title = t["win_dl_title"] if rel else t["win_title"]
            desc = t["win_dl_meta_desc"] if rel else t["win_meta_desc"]
            win_url = page_link(lang, "windows")
            graph = [
                {"@type": "WebPage", "name": plain(title), "url": win_url, "description": plain(desc),
                 "inLanguage": LANGS[lang][0], "dateModified": self.updated, "isPartOf": {"@type": "WebSite", "name": "Menew", "url": url(lang)}},
                {"@type": "BreadcrumbList", "itemListElement": [
                    {"@type": "ListItem", "position": 1, "name": "Menew", "item": url(lang)},
                    {"@type": "ListItem", "position": 2, "name": "Windows", "item": win_url}]},
            ]
            if rel:
                graph.insert(1, {"@type": "SoftwareApplication", "name": "Menew for Windows", "alternateName": ["Menew Windows"],
                                 "url": win_url, "description": plain(desc), "inLanguage": LANGS[lang][0],
                                 "operatingSystem": "Windows", "applicationCategory": "UtilitiesApplication",
                                 "softwareVersion": rel["tag"].lstrip("v"), "downloadUrl": rel["url"],
                                 "offers": {"@type": "Offer", "price": "0", "priceCurrency": "USD"}, "isAccessibleForFree": True,
                                 "license": "https://www.gnu.org/licenses/gpl-3.0.html", "author": self.org, "publisher": self.org,
                                 "sameAs": [repo_url]})
            self.platform_page("windows", lang, {"@context": "https://schema.org", "@graph": graph}, {
                "page_title": title, "page_desc": desc,
                "robots": "index, follow" if rel else "noindex, follow",
                "release_url": rel["url"] if rel else f"{repo_url}/releases",
                "release_version": rel["tag"] if rel else "",
            }, flags={"released": bool(rel)})
        print("windows: 5 languages, " + (f"download page ({rel['tag']})" if rel else "coming soon (noindex, not in the sitemap)"))

    # -- SEO files -----------------------------------------------------------------------------

    def build_seo(self):
        entries = ""

        def add(page):
            nonlocal entries
            alternates = "".join(f'\n    <xhtml:link rel="alternate" hreflang="{LANGS[c][0]}" href="{page_link(c, page) if page else url(c)}"/>' for c in LANGS)
            alternates += f'\n    <xhtml:link rel="alternate" hreflang="x-default" href="{page_link("en", page) if page else url("en")}"/>'
            entries += "".join(f"\n  <url>\n    <loc>{page_link(c, page) if page else url(c)}</loc>\n    <lastmod>{self.updated}</lastmod>{alternates}\n  </url>"
                               for c in LANGS)

        add("")
        entries += "".join(f"\n  <url>\n    <loc>{SITE}/vs{p}</loc>\n    <lastmod>{self.updated}</lastmod>\n  </url>"
                           for p in [""] + [f"/{slug}" for slug in self.compare])
        add("linux")
        add("mac")
        if self.win_released:
            add("windows")
        write(self.out, "sitemap.xml", '<?xml version="1.0" encoding="UTF-8"?>\n'
              '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:xhtml="http://www.w3.org/1999/xhtml">'
              + entries + "\n</urlset>\n")
        write(self.out, "robots.txt", f"User-agent: *\nAllow: /\n\nSitemap: {SITE}/sitemap.xml\n")

        # llms.txt (llmstxt.org): a plain-text summary for AI assistants (ChatGPT, Claude, Perplexity…) to read and cite
        e = self.shared["en"]
        n_faq = sum(1 for k in e if re.fullmatch(r"faq\d+_q", k))
        n_rep = sum(1 for k in e if re.fullmatch(r"rep\d+_t", k))
        linux_state = "early preview releases for testers: " if self.linux_pre else ""
        if self.win_released:
            windows_line = (f"- Menew for Windows ({self.win_rel['tag']}"
                            + (", preview" if self.win_rel["prerelease"] else "")
                            + f"; command palette, clipboard history and notes): {SITE}/windows (also {SITE}/pt/windows, {SITE}/de/windows, "
                            f"{SITE}/es/windows, {SITE}/zh/windows); source: https://github.com/Quost/menew-windows")
        else:
            windows_line = (f"- Menew for Windows: in progress, no release yet (command palette, clipboard history and notes); "
                            f"watch https://github.com/Quost/menew-windows for releases ({SITE}/windows)")
        lines = [
            "# Menew", "",
            f"> {plain(e['meta_desc'])}", "",
            f"Menew {self.version} (updated {self.updated}). Native Swift/SwiftUI app for macOS 14 or later, Apple Silicon and Intel. "
            "Free and open source (GPL-3.0). Signed with an Apple Developer ID and notarized. "
            "Made by Pururucos (https://pururucos.com.br), a software house in Brazil; the source code is at github.com/Quost/menew.", "",
            "## Highlights", "",
            *[f"- {plain(e[f'b{i}_t'])}: {plain(e[f'b{i}_d'])}" for i in range(1, 6)], "",
            "## Capabilities in detail", "",
            *[f"- {name}: {text}" for name, text in FACTS], "",
            "## Not included", "",
            NOT_INCLUDED, "",
            "## Name", "",
            "Menew (also written Menew for Mac) is the macOS app by Pururucos, published at github.com/Quost/menew. It is not related to Linx Menew, a restaurant point-of-sale system in Brazil.", "",
            "## One app instead of many", "",
            *[f"- Instead of {e[f'rep{i}_t'].replace(' · ', ' or ')}: {plain(e[f'rep{i}_d'])}" for i in range(1, n_rep + 1)], "",
            "## Features", "",
            *[f"- {plain(e[f'f{i}_t'])}: {plain(e[f'f{i}_d'])}" for i in range(1, 7)], "",
            "## More features", "",
            *[f"- {plain(e[f'act_l{i}'])}" for i in range(1, 11)],
            *[f"- {plain(e[f'nat{i}_t'])}: {plain(e[f'nat{i}_d'])}" for i in range(1, 8)],
            *[f"- {plain(e[f'pro{i}_t'])}: {plain(e[f'pro{i}_d'])}" for i in range(1, 7)], "",
            "## FAQ", "",
            *[f"### {plain(e[f'faq{i}_q'])}\n\n{plain(e[f'faq{i}_a'])}\n" for i in range(1, n_faq + 1)],
            "## Install", "",
            f"- Mac download page (DMG, Homebrew, from source, requirements): {SITE}/mac",
            "- Download: https://github.com/Quost/menew/releases/latest/download/Menew.dmg",
            "- Homebrew: `brew tap quost/menew https://github.com/Quost/menew && brew install --cask menew`", "",
            "## Links", "",
            f"- Website: {SITE}/ (also in Portuguese, German, Spanish and Chinese: {SITE}/pt, {SITE}/de, {SITE}/es, {SITE}/zh)",
            "- Source code and README: https://github.com/Quost/menew",
            f"- Menew for Linux ({linux_state}command palette, clipboard history, notes, screenshots, QR codes and system monitor for GNOME and KDE Plasma): {SITE}/linux (also {SITE}/pt/linux, {SITE}/de/linux, {SITE}/es/linux, {SITE}/zh/linux); source: https://github.com/Quost/menew-linux",
            windows_line,
            *[f"- Menew vs {c['other']}: {SITE}/vs/{slug}" for slug, c in self.compare.items()],
            "- Releases and changelog: https://github.com/Quost/menew/releases",
            f"- Made by Pururucos: {self.maker['url']} (other apps: "
            + ", ".join(f"{p['name']} {p['url']}" for p in self.family["products"] if p["id"] != "menew") + ")",
        ]
        write(self.out, "llms.txt", "\n".join(lines) + "\n")

        # build.json: what this build was made from; the scheduled workflow compares its fingerprint before rebuilding
        info = {"fingerprint": self.fingerprint, "site": self.site_commit, "updated": self.updated,
                "built_at": datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat(),
                "platforms": {p: {"repo": s["repo"], "content": s["content"], "commits": s.get("commits", []), "release": s["release"]}
                              for p, s in self.states.items()}}
        write(self.out, "build.json", json.dumps(info, indent=2) + "\n")
        print("sitemap.xml, robots.txt, llms.txt, build.json")

    def run(self):
        self.copy_files()
        self.build_overview()
        self.build_compare()
        self.build_mac()
        self.build_linux()
        self.build_windows()
        self.build_seo()


# ---------------------------------------------------------------- local preview

class CleanUrlHandler(http.server.SimpleHTTPRequestHandler):
    """Serves /mac from mac/index.html without a redirect, like Firebase Hosting with cleanUrls and no trailing slash"""

    def send_head(self):
        p = self.path.split("?", 1)[0].split("#", 1)[0]
        local = Path(self.translate_path(p))
        if p != "/" and local.is_dir() and (local / "index.html").exists():
            self.path = p.rstrip("/") + "/index.html"
        return super().send_head()


def serve(out, port):
    handler = functools.partial(CleanUrlHandler, directory=str(out))
    with http.server.ThreadingHTTPServer(("127.0.0.1", port), handler) as httpd:
        print(f"Preview: http://localhost:{port}/  (Ctrl+C to stop)")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass


def parse_overrides(items):
    overrides = {}
    for p in ("mac", "linux", "windows"):
        if os.environ.get(f"MENEW_CONTENT_{p.upper()}"):
            overrides[p] = os.environ[f"MENEW_CONTENT_{p.upper()}"]
    for item in items or []:
        if "=" not in item:
            raise SystemExit(f"--content expects platform=path, got {item!r}")
        p, where = item.split("=", 1)
        overrides[p.strip()] = where.strip()
    return overrides


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", default=str(ROOT / "public"), help="output folder (default: public/)")
    ap.add_argument("--content", action="append", metavar="PLATFORM=PATH",
                    help="local content folder for a platform (its site/ folder), or 'fallback' for Windows")
    ap.add_argument("--offline", action="store_true", help="no network: needs --content for mac and linux; no versions")
    ap.add_argument("--serve", nargs="?", const=8000, type=int, metavar="PORT", help="preview the result (default port 8000)")
    ap.add_argument("--no-check", action="store_true", help="skip the output checks (scripts/check.py)")
    args = ap.parse_args()
    out = Path(args.out).resolve()
    # The output folder is wiped on every build: never point it at something else
    if out == ROOT or out in ROOT.parents or (out.exists() and any(out.iterdir()) and not (out / "build.json").exists()):
        raise SystemExit(f"refusing to wipe {out}: use an empty folder or a previous build (the default is public/)")
    overrides = parse_overrides(args.content)
    unknown = set(overrides) - {"mac", "linux", "windows"}
    if unknown:
        raise SystemExit(f"unknown platform(s) in --content: {', '.join(sorted(unknown))}")
    b = Build(out, overrides, args.offline)
    b.run()
    if not args.no_check:
        import check
        problems = check.check(out)
        if problems:
            print("\n".join(f"  - {p}" for p in problems), file=sys.stderr)
            raise SystemExit(f"{len(problems)} problem(s) in the generated site")
        print("checks: ok")
    print(f"built {out} (fingerprint {b.fingerprint[:12]})")
    if args.serve:
        serve(out, args.serve)


if __name__ == "__main__":
    main()
