#!/usr/bin/env python3
"""Finds the official image and the matching GPL archive for a FRITZ!Box model on AVM's servers."""
import argparse
import json
import re
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor

DOWNLOAD = "https://download.avm.de/fritzbox"
OSP = "https://osp.avm.de/fritzbox"


def listing(url: str) -> list[str]:
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            html = r.read().decode("latin-1")
    except OSError:
        return []
    return [h for h in re.findall(r'href="([^"?/][^"]*)"', html) if h != "../"]


def version_key(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in v.split("."))


def resolve(model: str, version: str, regions: list[str]) -> dict:
    for region in regions:
        base = f"{DOWNLOAD}/{model}/{region}/fritz.os/"
        images = {m.group(1): f for f in listing(base) if (m := re.search(r"-(\d+\.\d+)\.image$", f))}
        if images:
            break
    else:
        known = sorted(h.rstrip("/") for h in listing(f"{DOWNLOAD}/") if h.startswith("fritzbox-"))
        raise SystemExit(f"no image for {model!r}; models on {DOWNLOAD}: {' '.join(known)}")
    if version == "latest":
        version = max(images, key=version_key)
    if version not in images:
        raise SystemExit(f"{model} {version} not offered, available: {' '.join(sorted(images, key=version_key))}")
    archives = [f for f in listing(f"{OSP}/{model}/") if f.startswith("source-files-") and f.endswith(f"-{version}.tar.gz")]
    if not archives:
        raise SystemExit(f"AVM has not published the GPL archive for {model} {version} yet")
    return {
        "model": model,
        "version": version,
        "region": region,
        "image_name": images[version],
        "image_url": base + images[version],
        "archive_name": archives[0],
        "archive_url": f"{OSP}/{model}/{archives[0]}",
    }


def buildable(regions: list[str]) -> list[str]:
    """Every model with a current image and its GPL archive, the set a daily check can cover."""
    def ok(model: str) -> bool:
        try:
            resolve(model, "latest", regions)
            return True
        except SystemExit:
            return False
    models = sorted(h.rstrip("/") for h in listing(f"{DOWNLOAD}/") if h.startswith("fritzbox-"))
    with ThreadPoolExecutor(8) as pool:
        return [m for m, good in zip(models, pool.map(ok, models)) if good]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("model", nargs="?", help="AVM directory name, for example fritzbox-7590-ax")
    ap.add_argument("--list", action="store_true", help="print a JSON list of every buildable model")
    ap.add_argument("--version", default="latest")
    ap.add_argument("--regions", default="deutschland,other,english,international")
    ap.add_argument("--env", action="store_true", help="print KEY=value lines instead of JSON")
    a = ap.parse_args()
    if a.list:
        print(json.dumps(buildable(a.regions.split(","))))
        return 0
    if not a.model:
        ap.error("model is required without --list")
    r = resolve(a.model, a.version, a.regions.split(","))
    print("\n".join(f"{k.upper()}={v}" for k, v in r.items()) if a.env else json.dumps(r, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
