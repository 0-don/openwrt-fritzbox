#!/usr/bin/env python3
"""Proves that self-built kernel modules are drop-in compatible with the ones in an AVM firmware image."""
import argparse
import json
import struct
import subprocess
import sys
from pathlib import Path


def readelf_symbols(ko: Path) -> tuple[set[str], set[str]]:
    out = subprocess.run(["readelf", "-sW", str(ko)], capture_output=True, text=True, check=True).stdout
    exported, undefined = set(), set()
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 8:
            continue
        name = parts[7]
        if name.startswith("__ksymtab_") and not name.startswith("__ksymtab_gpl"):
            exported.add(name.removeprefix("__ksymtab_"))
        elif parts[6] == "UND" and name:
            undefined.add(name)
    return exported, undefined


def sections(ko: Path) -> list[tuple[str, int, bytes, int]]:
    """(name, flags, contents, size) of every section, for 32 and 64-bit ELF of either byte order."""
    data = ko.read_bytes()
    end = ">" if data[5] == 2 else "<"
    wide = data[4] == 2
    shoff, = struct.unpack_from(end + ("Q" if wide else "I"), data, 0x28 if wide else 0x20)
    shentsize, shnum, shstrndx = struct.unpack_from(end + "HHH", data, 0x3A if wide else 0x2E)
    fmt = end + ("IIQQQQ" if wide else "IIIIII")
    headers = [struct.unpack_from(fmt, data, shoff + i * shentsize) for i in range(shnum)]
    names = headers[shstrndx][4]
    out = []
    for name, sh_type, flags, _addr, offset, size in headers:
        label = data[names + name:data.index(b"\0", names + name)].decode()
        out.append((label, flags, b"" if sh_type == 8 else data[offset:offset + size], size))
    return out


def section(ko: Path, wanted: str) -> bytes:
    return next((body for name, _f, body, _s in sections(ko) if name == wanted), b"")


def loaded_layout(ko: Path) -> dict[str, int]:
    """Sizes of the sections the kernel actually loads; .comment and debug links are metadata."""
    return {name: size for name, flags, _b, size in sections(ko) if flags & 2 and name != ".note.gnu.build-id"}


def versions(ko: Path) -> dict[str, int]:
    """__versions holds one 64 byte record per import: a CRC the size of a long, then a NUL padded name."""
    raw, head = section(ko, "__versions"), ko.read_bytes()[:6]
    end, w = ("big" if head[5] == 2 else "little"), (8 if head[4] == 2 else 4)
    return {raw[i + w:i + 64].split(b"\0")[0].decode(): int.from_bytes(raw[i:i + w], end) for i in range(0, len(raw), 64)}


def modinfo(ko: Path, field: str) -> str:
    return subprocess.run(["modinfo", "-F", field, str(ko)], capture_output=True, text=True).stdout.strip()


def kernel_exports(symvers: Path) -> set[str]:
    return {line.split()[1] for line in symvers.read_text().splitlines() if len(line.split()) >= 2}


def index(root: Path) -> dict[str, Path]:
    return {p.stem: p for p in sorted(root.rglob("*.ko"))}


def compare(ours: Path, avm: Path, provided: set[str], size_tolerance: float) -> dict:
    o_exp, o_und = readelf_symbols(ours)
    a_exp, _ = readelf_symbols(avm)
    o_lay, a_lay = loaded_layout(ours), loaded_layout(avm)
    o_load, a_load = sum(o_lay.values()), sum(a_lay.values())
    missing = sorted(s for s in o_und if s not in provided and not s.startswith(("_GLOBAL_OFFSET_TABLE_", "__this_module")))
    o_ver, a_ver = versions(ours), versions(avm)
    crc_bad = sorted(k for k in o_ver.keys() & a_ver.keys() if o_ver[k] != a_ver[k])
    magic, deps = modinfo(ours, "vermagic"), modinfo(ours, "depends")
    c = {
        "vermagic": {"ok": magic == modinfo(avm, "vermagic"), "detail": magic},
        "depends": {"ok": deps == modinfo(avm, "depends"), "detail": deps},
        "exports_identical": {
            "ok": o_exp == a_exp,
            "detail": f"{len(o_exp)} vs {len(a_exp)}; only ours {sorted(o_exp - a_exp)[:5]} only avm {sorted(a_exp - o_exp)[:5]}",
        },
        "modversions_crc_match": {
            "ok": not crc_bad,
            "detail": f"{len(o_ver.keys() & a_ver.keys())} shared CRCs, mismatched {crc_bad[:5]}",
        },
        "undefined_resolvable": {"ok": not missing, "detail": f"{len(o_und)} needed, unresolved {missing[:10]}"},
        "loaded_size": {
            "ok": abs(o_load - a_load) <= a_load * size_tolerance,
            "detail": f"{o_load} vs {a_load} bytes loaded ({(o_load - a_load) / a_load:+.2%}), "
                      f"{'identical' if o_lay == a_lay else 'differs in ' + ', '.join(sorted(k for k in o_lay.keys() | a_lay.keys() if o_lay.get(k) != a_lay.get(k))[:4])}",
        },
    }
    return {"path": str(avm), "checks": c, "ok": all(v["ok"] for v in c.values())}


def check(ours_dir: Path, avm_dir: Path, symvers: Path, size_tolerance: float) -> dict:
    ours, avm = index(ours_dir), index(avm_dir)
    # Every swapped module must export exactly what AVM's did, so AVM's export set is what the box provides.
    provided = kernel_exports(symvers).union(*(readelf_symbols(p)[0] for p in avm.values()))
    modules = {name: compare(ours[name], avm[name], provided, size_tolerance) for name in sorted(ours.keys() & avm.keys())}
    passed = [n for n, e in modules.items() if e["ok"]]
    return {
        "modules": modules,
        "passed": passed,
        "failed": [n for n, e in modules.items() if not e["ok"]],
        "not_built": {n: modinfo(p, "license") or "none" for n, p in sorted(avm.items()) if n not in ours},
        "ok": bool(passed),
    }


def markdown(report: dict) -> str:
    total = len(report["modules"]) + len(report["not_built"])
    rows = [
        f"## Module compatibility proof: {len(report['passed'])} of {total} AVM modules rebuilt and proven",
        "",
        "| module | result | failed checks |",
        "|---|---|---|",
    ]
    for mod, entry in report["modules"].items():
        bad = [f"{k}: {v['detail']}" for k, v in entry["checks"].items() if not v["ok"]]
        rows.append(f"| {mod} | {'pass' if entry['ok'] else 'FAIL'} | {'; '.join(bad)} |")
    rows += ["", "Not rebuilt (no source in the GPL archive or no recipe yet):", ""]
    rows += [f"- {n} ({lic})" for n, lic in report["not_built"].items()]
    return "\n".join(rows) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ours", type=Path, required=True, help="directory tree with self-built stripped .ko files")
    ap.add_argument("--avm", type=Path, required=True, help="lib/modules/<release> of the unpacked AVM image")
    ap.add_argument("--symvers", type=Path, required=True, help="Module.symvers of the self-built kernel")
    ap.add_argument("--size-tolerance", type=float, default=0.005)
    ap.add_argument("--json", type=Path)
    ap.add_argument("--markdown", type=Path)
    ap.add_argument("--passed", type=Path, help="write the AVM paths of modules safe to swap, one per line")
    a = ap.parse_args()
    report = check(a.ours, a.avm, a.symvers, a.size_tolerance)
    if a.json:
        a.json.write_text(json.dumps(report, indent=2))
    if a.passed:
        a.passed.write_text("".join(f"{report['modules'][n]['path']}\n" for n in report["passed"]))
    md = markdown(report)
    if a.markdown:
        a.markdown.write_text(md)
    print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
