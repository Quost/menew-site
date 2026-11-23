#!/usr/bin/env python3
"""Refreshes the local copy of the Pururucos product list (the "More apps by Pururucos" band and the footer credit).

    python3 scripts/sync-family.py                      # downloads https://pururucos.com.br/products.json and the images
    python3 scripts/sync-family.py --json products.json # uses a local file (e.g. before the Pururucos site is deployed)

Writes site/family.json and the images (other apps' icons and the Pururucos logo) to static/assets/family/.
It does NOT run in the build: scripts/build.py only reads the local copy, so the build stays deterministic.
Run it by hand when Pururucos launches or changes a product, check the diff, run the build and commit.
"""
import argparse, json, sys, urllib.request
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
SOURCE = "https://pururucos.com.br/products.json"
OUT_JSON = ROOT / "site/family.json"
OUT_IMG = ROOT / "static/assets/family"
LANGS = ("en", "pt", "de", "es", "zh")
IMAGE_TYPES = (".webp", ".png", ".svg", ".jpg", ".jpeg")

def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "menew-site-sync"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()

def local_name(url):
    """Local file name of a remote image (scripts/build.py uses the same rule)"""
    name = Path(urlparse(url).path).name
    assert name and Path(name).suffix.lower() in IMAGE_TYPES, f"imagem inesperada: {url}"
    return name

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", help="arquivo local no lugar de " + SOURCE)
    ap.add_argument("--images", help="pasta com as imagens (ex.: public/ do site da Pururucos), no lugar de baixar")
    args = ap.parse_args()

    data = json.loads(Path(args.json).read_text(encoding="utf-8") if args.json else fetch(SOURCE).decode("utf-8"))
    maker = data["maker"]
    for key in ("credit", "about", "cta", "othersTitle"):
        assert set(LANGS) <= set(maker[key]), f"maker.{key}: faltam idiomas"
    for key in ("title", "body"):
        assert set(LANGS) <= set(maker["yourIdea"][key]), f"maker.yourIdea.{key}: faltam idiomas"
    for p in data["products"]:
        assert set(LANGS) <= set(p["tagline"]), f"{p['id']}: faltam idiomas no tagline"

    # Só as imagens que o site mostra: o logo e os ícones dos outros apps (Menew's own icon comes from the Mac repo's assets)
    urls = [maker["logo"]] + [p["icon"] for p in data["products"] if p["id"] != "menew"]
    OUT_IMG.mkdir(parents=True, exist_ok=True)
    keep = set()
    for url in urls:
        name = local_name(url)
        if args.images:
            body = (Path(args.images) / urlparse(url).path.lstrip("/")).read_bytes()
        else:
            body = fetch(url)
        (OUT_IMG / name).write_bytes(body)
        keep.add(name)
        print(f"static/assets/family/{name} ({len(body)} bytes)")
    for old in OUT_IMG.iterdir():
        if old.name not in keep:
            old.unlink()
            print(f"removed: static/assets/family/{old.name}")

    OUT_JSON.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{OUT_JSON.relative_to(ROOT)}: {len(data['products'])} products")
    print("Now: python3 scripts/build.py --serve")

if __name__ == "__main__":
    sys.exit(main())
