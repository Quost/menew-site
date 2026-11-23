# menew-site: rules for agents

The website https://menew.pururucos.com.br, built with Python (standard library only) from this repo plus the `site/`
folder of each app repo (Quost/menew, Quost/menew-linux, Quost/menew-windows) and their GitHub Releases. Read
`README.md` first: it describes the architecture, the content contract and the deploy.

## Where a change goes

- Layout, CSS, JS, overview page, `/vs` pages, shared strings, SEO, Pururucos band, deploy: **here**.
- Text or screenshot of `/mac`, `/linux` or `/windows`: **in that app's repo** (`site/content.json`, assets). Never copy
  app content or screenshots into this repo, and never edit the generated `public/`.
- A version number or download link: nowhere. They come from GitHub Releases at build time. Never hard-code one.
- Windows going live is automatic (first published release of Quost/menew-windows). There is no flag to flip.

## Always

- Every text in 5 languages: `en`, `pt`, `de`, `es`, `zh` (`site/i18n/<lang>.json` here; `texts.<lang>` in the app repos).
  Same keys in every file. No em-dash. Be honest about each port's state: what works today, no promises or dates.
- A new `{{key}}` in a platform template is a contract change for that app repo: add the key to its `site/content.json`
  in all 5 languages in a pull request there first (or put it in the shared strings and in `platforms.json` → `shared`).
  Removing or renaming keys the same way. Keep `contract` at `1` unless the format itself changes.
- Keep URLs stable (`/`, `/<lang>`, `/<page>`, `/<lang>/<page>`, `/vs/<slug>`, `/assets/...`): they are indexed.
  The canonical, hreflang, sitemap and JSON-LD must keep matching (`scripts/check.py` enforces this).
- Never link to `menew.web.app` or `menew-prd.web.app`; they only 301 to the new domain.
- Before pushing: `python3 scripts/build.py` (on Windows with `PYTHONUTF8=1`). It must end with `checks: ok`.
  To test an app repo branch: `--content mac=../menew/site` (or `linux=`, `windows=`).
- Python standard library only; no npm/pip dependencies in the build. firebase-tools runs only in the deploy job, pinned.
- Workflows: actions pinned by full commit SHA with a `# vX.Y.Z` comment, minimal `permissions`, no long-lived secrets
  (deploy is keyless via `WIF_PROVIDER` / `WIF_SERVICE_ACCOUNT`). Never add a cross-repo token: the scheduled run polls.

## Never

- Never flip `DEPLOY_ENABLED`, deploy by hand, or change the Firebase project, sites or targets without the maintainer
  asking. One site per target: `menew` → `menew-pururucos`, `menew-legacy` → `menew`, `menew-legacy-prd` → `menew-prd`.
- Never add cookies, analytics other than GoatCounter (cookieless), or third-party embeds.
- Never weaken the content validator (`scripts/content.py`) or the output checks (`scripts/check.py`) to make a build
  pass; fix the content or the template.
- Never rename the IndexNow key file in `static/`.

## Commits

- Conventional Commits in English (`feat(site): …`, `fix(seo): …`, `ci: …`, `docs: …`), signed (`git commit -S`).
- Pull request titles follow the same format. Never force-push `main` or rewrite history without explicit approval.
