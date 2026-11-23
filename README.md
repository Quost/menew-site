# menew-site

The website of Menew: **https://menew.pururucos.com.br** (Firebase Hosting, project `menew-prd`).

This repository owns the layout, the overview page, the comparison pages, the shared strings, SEO and the deploy. The
text and screenshots of each platform page live in that platform's own repository, so the people who change an app
also change its page, in the same pull request.

| Part of the site | Lives in |
| --- | --- |
| Layout and look (`site/templates/*.html`), overview `/` `/pt` `/de` `/es` `/zh`, comparisons `/vs` and `/vs/*` | this repo |
| Shared strings (navigation, footer, overview texts) in 5 languages | this repo, `site/i18n/<lang>.json` |
| "More apps by Pururucos" band and footer credit | this repo, `site/family.json` + `static/assets/family/` |
| SEO: canonical, hreflang, `sitemap.xml`, `robots.txt`, `llms.txt`, JSON-LD, og images, IndexNow | this repo |
| Hosting config: `firebase.json`, `.firebaserc`, `hosting-redirect/` | this repo |
| `/mac` texts and screenshots (also the screenshots on the overview) | [Quost/menew](https://github.com/Quost/menew) `site/` |
| `/linux` texts and screenshots | [Quost/menew-linux](https://github.com/Quost/menew-linux) `site/` |
| `/windows` texts (and screenshots, once there are some) | [Quost/menew-windows](https://github.com/Quost/menew-windows) `site/` |
| Versions, download links, preview/stable labels, whether Windows is released | GitHub Releases of each app repo, read at build time |

Generated files are never committed: CI builds the site into `public/` and deploys it.

## How it works

```
 Quost/menew          site/content.json + docs/assets/   ─┐
 Quost/menew-linux    site/content.json + docs/assets/   ─┼─ shallow sparse clone of main ─┐
 Quost/menew-windows  site/content.json (+ assets)       ─┘                                │
 GitHub Releases API  latest release of each repo  ────────────────────────────────────────┤
                                                                                           ▼
 this repo: templates, shared strings, static files ──────────────────────▶ scripts/build.py ─▶ public/ ─▶ checks ─▶ Firebase
```

`scripts/build.py` (Python standard library only):

1. copies `static/` (IndexNow key, og images, Pururucos images) into `public/`;
2. for each platform in `platforms.json`, takes the app repo's `site/` folder from `main` (or a local folder, see
   below), validates it against the content contract (`scripts/content.py`) and copies its screenshots;
3. asks the GitHub Releases API for each app's latest release;
4. renders every page in the 5 languages, plus `sitemap.xml`, `robots.txt`, `llms.txt` and `build.json`;
5. runs `scripts/check.py` on the result. Any problem fails the build, so nothing broken is ever deployed.

## Content contract (for app repositories)

Each app repository has a `site/` folder at its root:

```
site/
  content.json   required: the page texts in 5 languages
  assets/        optional: screenshots (or point "assets" elsewhere in the repo, see below)
  README.md      recommended: a short note for contributors
```

`site/content.json`:

```json
{
  "contract": 1,
  "platform": "linux",
  "assets": "../docs/assets",
  "texts": {
    "en": { "title": "Menew for Linux: …", "h1": "Menew for <span class=\"grad\">Linux</span>", "…": "…" },
    "pt": { "…": "…" },
    "de": { "…": "…" },
    "es": { "…": "…" },
    "zh": { "…": "…" }
  }
}
```

| Field | Meaning |
| --- | --- |
| `contract` | Always `1` for now. A future incompatible change bumps it. |
| `platform` | `mac`, `linux` or `windows` (the key in `platforms.json`). |
| `assets` | Optional. Folder with the screenshots, relative to `site/`, inside the repository. Default `assets` (that is, `site/assets/`). The Mac and Linux repos use `../docs/assets`, the folder their README already uses, so there is a single copy of each image. |
| `texts` | One object per language: `en`, `pt`, `de`, `es`, `zh`, no others. Every language has exactly the keys of `en`. Values are non-empty strings and may contain a little inline HTML: `<b>`, `<strong>`, `<i>`, `<em>`, `<code>`, `<kbd>`, `<span class="…">`, `<br>`, `<small>`, `<sup>`, `<sub>`, `<a href="https://…">`. No scripts, styles, iframes or event handlers. |

Which keys a page needs is decided by its template here: every `{{key}}` in `site/templates/<platform>.html` that is not
filled by the build or taken from the shared strings (`platforms.json` → `shared`), plus the keys the build reads
directly (`platforms.json` → `required`). The validator prints the exact list of what is missing.

Screenshots: English at the top of the assets folder (`palette-dark.png`), other languages in a subfolder named after the
language (`pt/palette-dark.png`). Allowed types: png, webp, jpg, gif, svg, mp4, webm. Other subfolders are ignored. When a
`.webp` sits next to a `.png`, pages use the WebP. Every screenshot a template shows through `{{shots}}/…` must exist for
all 5 languages. They are published under the platform's URL: Mac at `/assets/…` (the overview uses them too), Linux at
`/assets/linux/…`, Windows at `/assets/windows/…`.

Validate a content folder (what the app repos' CI runs, by checking out this repo):

```sh
python3 scripts/content.py linux ../menew-linux/site
```

Rules of the contract:

- A malformed `content.json`, a missing language, a missing key or a missing screenshot fails the build with a message
  that names the repository, the language and the key.
- Windows is the only platform with a fallback: if `Quost/menew-windows` has no `site/content.json` on `main`, the build
  uses `site/fallback/windows/` here (the "coming soon" texts).
- Content is merged into the HTML as written, so review content pull requests like code.

## Versions and releases

Nothing to flip by hand. At build time:

- **Mac**: the latest non-prerelease release of `Quost/menew` gives the version in the structured data and `llms.txt`
  (the download button always points to `releases/latest/download/Menew.dmg`).
- **Linux**: the newest release of `Quost/menew-linux`, prereleases included. A prerelease shows "Preview", a normal
  release shows "Stable", on `/linux` and on the overview card.
- **Windows**: while `Quost/menew-windows` has no published release, `/windows` is the "coming soon" page (`noindex`,
  not in the sitemap). As soon as it has one (draft releases do not count), `/windows` becomes a download page for that
  release, gets `index, follow` and joins the sitemap, the overview card and `/mac` show it as Preview or Stable.
  The texts for both states are in the Windows content (`win_*` and `win_dl_*`).

The pages also refresh the version and the download link in the browser from the same API, so they are current even
between builds.

## Automatic sync (no cross-repo tokens)

The app repositories never call this one. `.github/workflows/deploy.yml` runs:

- on every push to `main` and on manual runs: always builds (and deploys, when enabled);
- every 30 minutes: first computes a **fingerprint** with a handful of API calls (`python3 scripts/sources.py`):
  for each app repo, the SHA of the latest commit on `main` touching `site/` (and the assets folder its `content.json`
  points to) plus its latest release id and tag, and this repo's commit. If it equals the `fingerprint` in
  https://menew.pururucos.com.br/build.json (written by the last build, served with `Cache-Control: no-cache`), the run
  stops there. Otherwise it builds and deploys.

So a merged content change or a new release reaches the site within about 30 minutes, with no secrets in the app repos.
The scheduled poll only runs while `DEPLOY_ENABLED` is `true`.

## Deploy

- Keyless: `google-github-actions/auth` with Workload Identity Federation, using the repo variables `WIF_PROVIDER` and
  `WIF_SERVICE_ACCOUNT` (service account with Firebase Hosting Admin and Service Usage Consumer on `menew-prd`). No JSON key.
- Then `firebase-tools` (pinned) deploys three targets: `menew` → site `menew-pururucos` (menew.pururucos.com.br, folder
  `public/`), `menew-legacy` → site `menew` (menew.web.app) and `menew-legacy-prd` → site `menew-prd` (menew-prd.web.app).
  The two legacy sites only 301 every path to the new domain (`hosting-redirect/`). Firebase allows one site per target.
- After a deploy, `scripts/indexnow.py` pings IndexNow with every URL in the sitemap.
- **Deploying is gated by the repo variable `DEPLOY_ENABLED`**. Anything other than `true` builds, checks and uploads
  the result as the `site` artifact, without deploying.
- Actions are pinned by commit SHA; Dependabot proposes updates weekly.

## Pull requests

`.github/workflows/ci.yml` (job **Build and check site**) builds the whole site from the app repos' current `main` and
the latest releases and runs the same checks as a deploy. The built site is attached as an artifact.

## Preview locally

Python 3.10+ and git. No packages to install.

```sh
python3 scripts/build.py --serve                        # build from GitHub, then open http://localhost:8000
python3 scripts/build.py --content mac=../menew/site --content linux=../menew-linux/site --serve
python3 scripts/build.py --content windows=fallback     # force the Windows "coming soon" fallback
python3 scripts/build.py --offline --content mac=../menew/site --content linux=../menew-linux/site  # no network
```

`--content platform=path` points at a local `site/` folder (an app repo checkout, any branch). The same works with
environment variables: `MENEW_CONTENT_MAC`, `MENEW_CONTENT_LINUX`, `MENEW_CONTENT_WINDOWS`. With `--offline` the versions
show as `dev` and Windows uses the fallback. The GitHub API is used anonymously unless `GITHUB_TOKEN` is set or the
GitHub CLI is logged in (`gh auth token`). On Windows, run with `PYTHONUTF8=1` (`py -3` works too).

## Add a platform

1. In the app repo: add `site/content.json` (and screenshots) following the contract.
2. Here: add an entry to `platforms.json` (`repo`, `page`, `template`, `assets_url`, `release` = `stable` or `any`,
   `fallback`, `shared`, `required`, `screenshots`), a template in `site/templates/`, a `build_<platform>` method in
   `scripts/build.py` (and a call in `Build.run`), the platform in `PAGES` of `scripts/check.py` and in the sitemap
   (`build_seo`), and links to it from the overview.
3. In the app repo's CI, validate the content with this repo's validator (see the Mac or Linux repo for the job).
4. Open a pull request here; CI builds everything and checks every link.

## Other tasks

- **Pururucos band**: `python3 scripts/sync-family.py` refreshes `site/family.json` and `static/assets/family/` from
  https://pururucos.com.br/products.json. Manual on purpose, so the build never depends on that site.
- **Link-preview images** (`static/assets/og/<lang>.png`, 1200×630): `swift scripts/make-og.swift ../menew/docs/assets`
  on a Mac, after changing the overview title or badge, or when the Mac screenshots they show change. Commit the PNGs.
- **IndexNow key**: `static/df850856e8360dcdd128c85eb1c159fd.txt`. Public by design. Do not rename it.
- **Visit counter**: GoatCounter, cookieless (`GOATCOUNTER` in `scripts/build.py`). Never add cookies or another tracker.

## Layout

```
platforms.json            platforms: repo, page, template, asset URL, release channel, fallback, keys
site/templates/           overview.html, mac.html, linux.html, windows.html, compare.html
site/i18n/<lang>.json     shared strings and overview texts (5 languages)
site/compare.json         the /vs pages
site/family.json          Pururucos products (scripts/sync-family.py)
site/fallback/windows/    Windows "coming soon" content, used while menew-windows has none
static/                   copied as is: IndexNow key, assets/og, assets/family
scripts/build.py          the build (and --serve)
scripts/content.py        the content contract and its validator
scripts/sources.py        clones, GitHub API, fingerprint
scripts/check.py          output checks
scripts/indexnow.py       IndexNow ping
firebase.json .firebaserc hosting-redirect/   Firebase Hosting
```

## License

The code is GPL-3.0 (see [LICENSE](LICENSE)), like the apps. The name, logo, website texts, screenshots and visual
identity are not covered: see [TRADEMARK.md](TRADEMARK.md).
