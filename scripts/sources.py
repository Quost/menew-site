#!/usr/bin/env python3
"""Where the app repositories' content and releases come from, and the fingerprint of a build.

The build reads, for each platform in platforms.json:
  - its content (site/ of the app repo at main): a shallow, sparse git clone, or a local folder given with
    --content mac=../menew/site (or MENEW_CONTENT_MAC=...), or the site's fallback (Windows only);
  - its latest release (GitHub Releases API): version, link, prerelease flag;
  - the latest commit on main that touched its content (GitHub commits API), for the fingerprint and the page dates.

The fingerprint is a hash of: this repo's commit + each platform's content commit(s) + release id/tag. The build writes it
to /build.json; the scheduled workflow recomputes it from the API (a few small requests) and skips the build when the
live site already has it:

    python3 scripts/sources.py                      # print the current fingerprint and its inputs
    python3 scripts/sources.py --compare URL        # also compare with URL (the live build.json); sets changed=true|false
                                                    # in $GITHUB_OUTPUT when running in GitHub Actions

Set GITHUB_TOKEN (Actions does) to avoid the API's low anonymous rate limit; locally `gh auth token` is used if present.
"""
import datetime
import hashlib
import json
import os
import posixpath
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from content import ROOT, load_config

API = "https://api.github.com"
_TOKEN = None


def token():
    global _TOKEN
    if _TOKEN is None:
        _TOKEN = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
        if not _TOKEN and shutil.which("gh"):
            try:
                _TOKEN = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=10).stdout.strip()
            except (OSError, subprocess.SubprocessError):
                _TOKEN = ""
    return _TOKEN


def http_get(url, accept="application/vnd.github+json", auth=True, timeout=30):
    headers = {"Accept": accept, "User-Agent": "menew-site-build", "X-GitHub-Api-Version": "2022-11-28"}
    if auth and token() and url.startswith(API):
        headers["Authorization"] = f"Bearer {token()}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def api(path, accept="application/vnd.github+json", missing_ok=False, attempts=3):
    for attempt in range(1, attempts + 1):
        try:
            body = http_get(API + path, accept)
            return body if accept.endswith("raw") else json.loads(body)
        except urllib.error.HTTPError as e:
            if missing_ok and e.code == 404:
                return None
            if e.code < 500 or attempt == attempts:
                detail = e.read().decode("utf-8", "replace")[:300]
                raise SystemExit(f"GitHub API {path}: HTTP {e.code} {detail}") from None
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            if attempt == attempts:
                reason = getattr(e, "reason", e)
                raise SystemExit(f"GitHub API {path}: {reason} (use --offline to build without the network)") from None
        time.sleep(2 * attempt)


def pick_release(repo, channel):
    """Newest published release: "stable" skips prereleases, "any" takes them too. None when there is none."""
    for rel in api(f"/repos/{repo}/releases?per_page=20") or []:
        if rel.get("draft"):
            continue
        if channel == "stable" and rel.get("prerelease"):
            continue
        # the files people download (the Linux page links the .deb and the AppImage directly)
        files = {a["name"]: a["browser_download_url"] for a in rel.get("assets") or [] if a.get("state", "uploaded") == "uploaded"}
        return {"tag": rel["tag_name"], "id": rel["id"], "prerelease": bool(rel["prerelease"]),
                "url": rel["html_url"], "published": (rel.get("published_at") or "")[:10], "files": files}
    return None


def assets_path(doc):
    """Repo-relative folder of the screenshots declared by a content.json (default site/assets)."""
    p = posixpath.normpath(posixpath.join("site", doc.get("assets") or "assets"))
    return None if p.startswith("..") else p


def last_commit(repo, path):
    commits = api(f"/repos/{repo}/commits?sha=main&path={path}&per_page=1")
    if not commits:
        return None
    c = commits[0]
    return {"sha": c["sha"], "date": c["commit"]["committer"]["date"][:10]}


def remote_state(platform, cfg):
    """What the fingerprint needs for one platform, from the API only (no clone)."""
    repo = cfg["repo"]
    raw = api(f"/repos/{repo}/contents/site/content.json?ref=main", "application/vnd.github.raw", missing_ok=True)
    state = {"repo": repo, "release": pick_release(repo, cfg["release"])}
    if raw is None:
        if not cfg.get("fallback"):
            raise SystemExit(f"{platform}: {repo} has no site/content.json on main (was its site content PR merged?)")
        state.update(content="fallback", commits=[])
        return state
    try:
        doc = json.loads(raw)
    except json.JSONDecodeError:
        doc = {}
    paths = ["site"]
    ap = assets_path(doc)
    if ap and not (ap == "site" or ap.startswith("site/")):
        paths.append(ap)
    commits = [c for c in (last_commit(repo, p) for p in paths) if c]
    state.update(content="repo", paths=paths, commits=commits)
    return state


def local_state(platform, cfg, site_dir, offline=False):
    """Same as remote_state, for a local content folder (--content): commit info from its git history if any."""
    site_dir = Path(site_dir).resolve()
    commits = []
    try:
        top = subprocess.run(["git", "-C", str(site_dir), "rev-parse", "--show-toplevel"], capture_output=True, text=True,
                             check=True).stdout.strip()
        doc = json.loads((site_dir / "content.json").read_text(encoding="utf-8")) if (site_dir / "content.json").exists() else {}
        ap = posixpath.relpath(posixpath.normpath(posixpath.join(site_dir.as_posix(), doc.get("assets") or "assets")),
                               Path(top).resolve().as_posix())
        out = subprocess.run(["git", "-C", top, "log", "-1", "--format=%H %cs", "--", site_dir.as_posix(), ap],
                             capture_output=True, text=True).stdout.split()
        dirty = subprocess.run(["git", "-C", top, "status", "--porcelain", "--", site_dir.as_posix(), ap],
                               capture_output=True, text=True).stdout.strip()
        if out:
            commits.append({"sha": out[0] + ("-dirty" if dirty else ""),
                            "date": datetime.date.today().isoformat() if dirty else out[1]})
    except (subprocess.CalledProcessError, OSError, ValueError):
        pass
    if not commits:
        commits.append({"sha": "local", "date": datetime.date.today().isoformat()})
    release = None if offline else pick_release(cfg["repo"], cfg["release"])
    return {"repo": cfg["repo"], "release": release, "content": f"local:{site_dir.name}", "commits": commits}


def site_commit():
    sha = os.environ.get("GITHUB_SHA") or subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True,
                                                         text=True).stdout.strip() or "unknown"
    dirty = subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain", "--", "site", "static", "scripts", "platforms.json",
                            "firebase.json", "hosting-redirect"], capture_output=True, text=True).stdout.strip()
    return sha + ("-dirty" if dirty and not os.environ.get("GITHUB_ACTIONS") else "")


def fingerprint(site, states):
    """Stable hash of everything that changes the generated site."""
    essence = {"site": site, "platforms": {
        p: {"content": s["content"], "commits": [c["sha"] for c in s.get("commits", [])],
            "release": s["release"] and {"id": s["release"]["id"], "tag": s["release"]["tag"],
                                         "prerelease": s["release"]["prerelease"],
                                         # files are uploaded after the release is created: a build in between must not stick
                                         "files": sorted(s["release"].get("files") or {})}}
        for p, s in sorted(states.items())}}
    return hashlib.sha256(json.dumps(essence, sort_keys=True).encode()).hexdigest()


def clone(repo, workdir):
    """Shallow, sparse clone of main with only site/ (plus the assets folder that content.json points to)."""
    dest = Path(workdir) / repo.replace("/", "__")
    url = os.environ.get("MENEW_GIT_BASE", "https://github.com/") + repo + ".git"
    run = lambda *a: subprocess.run(["git", *a], check=True, capture_output=True, text=True)
    run("clone", "--quiet", "--depth", "1", "--filter=blob:none", "--sparse", "--branch", "main", url, str(dest))
    run("-C", str(dest), "sparse-checkout", "set", "site")
    doc_path = dest / "site/content.json"
    if doc_path.exists():
        try:
            ap = assets_path(json.loads(doc_path.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            ap = None  # content.load() reports the broken JSON
        if ap and ap != "site" and not ap.startswith("site/"):
            run("-C", str(dest), "sparse-checkout", "add", ap)
    return dest / "site"


def workdir():
    tmp = tempfile.mkdtemp(prefix="menew-site-")
    import atexit
    atexit.register(shutil.rmtree, tmp, True)
    return tmp


def main(argv):
    compare = None
    if "--compare" in argv:
        compare = argv[argv.index("--compare") + 1]
    config = load_config()
    states = {p: remote_state(p, cfg) for p, cfg in config["platforms"].items()}
    fp = fingerprint(site_commit(), states)
    print(json.dumps({"fingerprint": fp, "platforms": states}, indent=2))
    if compare:
        try:
            live = json.loads(http_get(f"{compare}?t={int(datetime.datetime.now().timestamp())}", "application/json",
                                       auth=False)).get("fingerprint")
        except (urllib.error.URLError, json.JSONDecodeError, AttributeError) as e:
            print(f"live build.json not available ({e}): rebuilding")
            live = None
        changed = live != fp
        print(f"live: {live}\nnow:  {fp}\nchanged: {str(changed).lower()}")
        if os.environ.get("GITHUB_OUTPUT"):
            with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as f:
                f.write(f"changed={str(changed).lower()}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
