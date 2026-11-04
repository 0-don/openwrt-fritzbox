#!/usr/bin/env python3
"""Hardware inventory of every FRITZ!Box AVM publishes: kernel, architecture and the drivers behind each feature."""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fwpack import extract  # noqa: E402
from resolve import buildable, resolve  # noqa: E402
from verify_modules import modinfo  # noqa: E402

FEATURES = {
    "wifi": r"^(mtlk|ath\d+k|ath9k|qca|wlan|wl$|dhd|mt7\d+|cfg80211$|mac80211$|wave|iwlwav|q6v5|bcm_wlan)",
    "dsl": r"(dsl|vdsl|adsl|vrx\d+|drv_mei|ltq_atm|ltq_ptm|xdsl|mei_)",
    "dect": r"dect",
    "telephony": r"(pcmlink|isdn|capi|krtp|nlaudio|tapi|vmmc|piglet|telefon|fxs|slic)",
    "cable": r"(docsis|puma|cm_|cablemodem)",
    "offload": r"(avm_pa|offload|ppa|nss|flow_offload)",
}
MACHINES = {0x08: "mips", 0x28: "arm", 0xB7: "arm64", 0x03: "x86", 0x3E: "x86_64"}


def elf_machine(ko: Path) -> str:
    head = ko.read_bytes()[:20]
    return MACHINES.get(int.from_bytes(head[18:20], "big" if head[5] == 2 else "little"), "unknown")


TOOLS = Path(os.environ.get("FREETZ_TOOLS", Path.home() / "src/fritz-build/freetz-ng/tools"))


def inspect(model: str, work: Path) -> list[dict]:
    r = resolve(model, "latest", ["deutschland", "other", "english", "international"])
    image = work / r["image_name"]
    if not image.exists():
        urllib.request.urlretrieve(r["image_url"], image)
    base = {"model": model, "version": r["version"], "image": r["image_name"]}
    try:
        roots = extract(image, work / "fw", TOOLS)
    except Exception as e:  # noqa: BLE001
        return [base | {"error": f"extract: {e}"}]
    finally:
        image.unlink(missing_ok=True)
    rows = []
    for root in roots:
        mods_root = root / "lib/modules"
        kos = sorted(mods_root.rglob("*.ko")) if mods_root.exists() else []
        releases = sorted({p.relative_to(mods_root).parts[0] for p in kos if len(p.relative_to(mods_root).parts) > 1})
        out = base | {"kernel": releases, "arch": elf_machine(kos[0]) if kos else "unknown", "modules": len(kos), "features": {}}
        for feature, pattern in FEATURES.items():
            hits = {}
            for ko in kos:
                if re.search(pattern, ko.stem, re.I):
                    lic = modinfo(ko, "license").splitlines()
                    hits[ko.stem] = lic[0] if lic else "none"
            out["features"][feature] = hits
        rows.append(out)
    shutil.rmtree(work / "fw", ignore_errors=True)
    return rows


def markdown(rows: list[dict]) -> str:
    def cell(hits: dict) -> str:
        if not hits:
            return ""
        closed = [n for n, lic in hits.items() if "GPL" not in lic]
        return f"{len(hits)} ({len(closed)} closed)" if closed else f"{len(hits)} open"
    lines = ["| model | FRITZ!OS | arch | kernel | Wi-Fi | DSL | DECT | telephony | cable | offload |", "|" + "---|" * 10]
    for r in rows:
        if "error" in r:
            lines.append(f"| {r['model']} | {r['version']} | {r['error'][:60]} |" + " |" * 7)
            continue
        f = r["features"]
        lines.append(f"| {r['model']} | {r['version']} | {r['arch']} | {', '.join(r['kernel'])} | "
                     + " | ".join(cell(f[k]) for k in FEATURES) + " |")
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("work", type=Path)
    ap.add_argument("--models", help="comma separated; default every buildable model")
    a = ap.parse_args()
    a.work.mkdir(parents=True, exist_ok=True)
    models = a.models.split(",") if a.models else buildable(["deutschland", "other", "english", "international"])
    rows = []
    for m in models:
        try:
            rows += inspect(m, a.work)
        except Exception as e:  # noqa: BLE001
            rows.append({"model": m, "version": "?", "error": str(e)})
        print(f"{m}: {rows[-1].get('arch', rows[-1].get('error'))}", file=sys.stderr, flush=True)
    (a.work / "inventory.json").write_text(json.dumps(rows, indent=2))
    (a.work / "inventory.md").write_text(markdown(rows))
    print(markdown(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
