#!/usr/bin/env python3
"""Reads what a GPL rebuild must match out of an unpacked FRITZ!OS root filesystem and AVM's GPL sources."""
import argparse
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from verify_modules import readelf_symbols, section, sections  # noqa: E402

DC_MODES = {"xrx500_": "XRX500", "xrx750_": "XRX750", "xrx330_": "330", "prx300_": "PRX300", "lgm_": "LGM"}
SOURCE_PATH = re.compile(rb"/[\w./+-]+\.[ch]\b")


def modules(rootfs: Path) -> tuple[str, dict[str, Path]]:
    dirs = [d for d in (rootfs / "lib/modules").iterdir() if (d / "modules.dep").exists() or (d / "kernel").is_dir()]
    if len(dirs) != 1:
        raise SystemExit(f"cannot pick the kernel release from {[d.name for d in dirs]}")
    return dirs[0].name, {p.stem: p for p in sorted(dirs[0].rglob("*.ko"))}


def gcc_version(kos: dict[str, Path]) -> str:
    for ko in kos.values():
        if m := re.search(rb"GCC: \([^)]*\) (\d+\.\d+\.\d+)", section(ko, ".comment")):
            return m.group(1).decode()
    raise SystemExit("no GCC version in any module .comment")


def directconnect(kos: dict[str, Path]) -> tuple[str, str]:
    """AVM's recipe for Intel's DirectConnect is not published; its binaries tell which API and SoC mode it used."""
    core, hw = kos.get("directconnect_datapath"), kos.get("dc_mode-hw")
    if not core:
        return "", ""
    und = readelf_symbols(core)[1]
    if "dp_alloc_port_ext" in und:
        flags = "-DHAVE_DATAPATH_EXT_API"
    elif "dp_alloc_port" in und:
        flags = "-DHAVE_DATAPATH_API -DDP_F_FASTPATH=DP_F_FAST_WLAN"
    else:
        flags = "-DHAVE_DATAPATH_AVM_SWMODE"
    strtab = section(hw, ".strtab").decode("latin-1") if hw else ""
    mode = next((v for k, v in DC_MODES.items() if k in strtab), "AVM_SWMODE")
    return flags + " -fno-inline-functions-called-once", mode


def source_paths(ko: Path) -> list[str]:
    return [m.decode() for _n, flags, body, _s in sections(ko) if flags & 2 for m in SOURCE_PATH.findall(body)]


def prefix_in(path: str, tree: Path) -> str | None:
    parts = path.split("/")
    for i in range(2, len(parts)):
        if ".." not in parts[:i] and (tree / "/".join(parts[i:])).is_file():
            return "/".join(parts[:i])
    return None


def prefixes(kos: dict[str, Path], kernel: Path, trees: dict[str, Path]) -> dict[str, str]:
    """AVM's __FILE__ strings carry their build directories; GCC's -fmacro-prefix-map can reproduce them."""
    own, headers = {k: Counter() for k in trees}, {k: Counter() for k in trees}
    for ko in kos.values():
        paths = source_paths(ko)
        hits = {key: [p for p in (prefix_in(x, tree) for x in paths) if p] for key, tree in trees.items()}
        owner = next((k for k in trees if k != "kernel" and hits[k]), "kernel")
        own[owner].update(hits[owner])
        if owner != "kernel":
            headers[owner].update(p for p in hits["kernel"] if p not in own[owner])
    out = {}
    for key in trees:
        if own[key]:
            out[f"PREFIX_{key.upper()}"] = own[key].most_common(1)[0][0]
        if headers[key]:
            out[f"HEADERS_{key.upper()}"] = headers[key].most_common(1)[0][0]
    return out


def backports_defconfig(tree: Path, kos: dict[str, Path]) -> list[str]:
    """Turns on exactly the backports options whose modules the image ships, plus the menus above them."""
    rule = re.compile(r"obj-\$\((CPTCFG_\w+)\)\s*\+?=\s*(.+)")
    wanted: dict[str, str] = {}
    dir_symbol: dict[Path, str] = {}
    module_dirs: list[Path] = []
    for mk in [*tree.rglob("Makefile"), *tree.rglob("Makefile.kernel"), *tree.rglob("Kbuild")]:
        for sym, objs in rule.findall(mk.read_text(errors="replace")):
            for obj in objs.split():
                if obj.endswith("/"):
                    dir_symbol[(mk.parent / obj).resolve()] = sym
                elif obj.endswith(".o") and Path(obj).stem in kos:
                    wanted[sym] = "m"
                    module_dirs.append(mk.parent.resolve())
    for d in module_dirs:
        while d != tree.resolve() and d != d.parent:
            if d in dir_symbol:
                wanted.setdefault(dir_symbol[d], "y")
            d = d.parent
    return [f"{k}={v}" for k, v in sorted(wanted.items())]


def freetz_symbols(dl_firmware: Path, image_name: str) -> list[str]:
    for line in dl_firmware.read_text().splitlines():
        if f'"{image_name}"' in line and " if " in line:
            cond = line.split(" if ", 1)[1]
            return [s for s in re.findall(r"!?\bFREETZ_\w+", cond) if not s.startswith("!")]
    raise SystemExit(f"Freetz-NG has no board entry for {image_name}; pass FREETZ_SYMBOLS by hand")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("image", help="facts from the image alone")
    p.add_argument("rootfs", type=Path)
    p = sub.add_parser("prefixes", help="AVM build directories for each source tree")
    p.add_argument("rootfs", type=Path)
    p.add_argument("kernel", type=Path)
    p.add_argument("trees", nargs="*", help="key=path")
    p = sub.add_parser("backports", help="defconfig for a backports tree")
    p.add_argument("rootfs", type=Path)
    p.add_argument("tree", type=Path)
    p = sub.add_parser("freetz", help="Freetz-NG board symbols for an image name")
    p.add_argument("dl_firmware", type=Path)
    p.add_argument("image_name")
    a = ap.parse_args()
    if a.cmd == "freetz":
        print(" ".join(freetz_symbols(a.dl_firmware, a.image_name)))
        return 0
    krel, kos = modules(a.rootfs)
    if a.cmd == "image":
        dc_flags, dc_mode = directconnect(kos)
        facts = {"KREL": krel, "GCC_VERSION": gcc_version(kos), "DC_FLAGS": dc_flags, "DC_MODE": dc_mode}
    elif a.cmd == "prefixes":
        trees = {"kernel": a.kernel} | {k: Path(v) for k, v in (t.split("=", 1) for t in a.trees)}
        facts = prefixes(kos, a.kernel, trees)
    else:
        print("\n".join(backports_defconfig(a.tree, kos)))
        return 0
    print("\n".join(f"{k}='{v}'" for k, v in facts.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
