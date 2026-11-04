#!/usr/bin/env python3
"""Unpacks and repacks FRITZ!OS .image files and proves a repack changed only what it was asked to."""
import argparse
import hashlib
import json
import shutil
import stat
import struct
import subprocess
import sys
import tarfile
from pathlib import Path

TI_MAGIC = 0xC453DE23
COMPRESSORS = {1: "gzip", 2: "lzma", 3: "lzo", 4: "xz", 5: "lz4", 6: "zstd"}


def cksum(data: bytes) -> int:
    return int(subprocess.run(["cksum"], input=data, capture_output=True, check=True).stdout.split()[0])


def strip_trailer(blob: bytes) -> bytes:
    """Both parts end in AVM's TI trailer: magic plus the POSIX cksum of everything before it, little endian."""
    magic, crc = struct.unpack("<II", blob[-8:])
    if magic != TI_MAGIC or cksum(blob[:-8]) != crc:
        raise ValueError("bad TI checksum trailer")
    return blob[:-8]


def squashfs_params(sqfs: bytes) -> dict:
    mkfs_time, block_size = struct.unpack_from("<II", sqfs, 8)
    compression, _block_log, flags = struct.unpack_from("<HHH", sqfs, 20)
    return {
        "mkfs_time": mkfs_time,
        "block_size": block_size,
        "compression": COMPRESSORS[compression],
        "tailends": bool(flags & 0x20),
        "exportable": bool(flags & 0x80),
    }


def unpack(image: Path, work: Path) -> None:
    shutil.rmtree(work, ignore_errors=True)
    (work / "tar").mkdir(parents=True)
    with tarfile.open(image) as t:
        members = [{"name": m.name, "mode": m.mode, "mtime": m.mtime, "dir": m.isdir()} for m in t.getmembers()]
        t.extractall(work / "tar", filter="data")
    tmp = work / "tar/var/tmp"
    sqfs = strip_trailer((tmp / "filesystem.image").read_bytes())
    strip_trailer((tmp / "kernel.image").read_bytes())
    (work / "rootfs.sqfs").write_bytes(sqfs)
    subprocess.run(["unsquashfs", "-q", "-n", "-d", str(work / "rootfs"), str(work / "rootfs.sqfs")], check=True)
    (work / "meta.json").write_text(json.dumps({"members": members, "squashfs": squashfs_params(sqfs)}, indent=2))


def repack(work: Path, out: Path) -> None:
    meta = json.loads((work / "meta.json").read_text())
    sq = meta["squashfs"]
    built = work / "rebuilt.sqfs"
    built.unlink(missing_ok=True)
    cmd = ["mksquashfs", str(work / "rootfs"), str(built), "-noappend", "-quiet", "-no-progress", "-all-root", "-no-xattrs",
           "-comp", sq["compression"], "-b", str(sq["block_size"]), "-mkfs-time", str(sq["mkfs_time"])]
    cmd += [] if sq["tailends"] else ["-no-tailends"]
    cmd += [] if sq["exportable"] else ["-no-exports"]
    subprocess.run(cmd, check=True)
    body = built.read_bytes()
    body += b"\0" * (-len(body) % 4096)
    (work / "tar/var/tmp/filesystem.image").write_bytes(body + struct.pack("<II", TI_MAGIC, cksum(body)))
    with tarfile.open(out, "w", format=tarfile.GNU_FORMAT) as t:
        for m in meta["members"]:
            path = work / "tar" / m["name"]
            ti = t.gettarinfo(str(path), arcname=m["name"])
            ti.uid = ti.gid = 0
            ti.uname = ti.gname = ""
            ti.mode, ti.mtime = m["mode"], m["mtime"]
            if m["dir"]:
                t.addfile(ti)
            else:
                with open(path, "rb") as f:
                    t.addfile(ti, f)


def manifest(root: Path) -> dict[str, str]:
    out = {}
    for p in sorted(root.rglob("*")):
        mode = p.lstat().st_mode
        if stat.S_ISLNK(mode):
            out[str(p.relative_to(root))] = f"link {p.readlink()}"
        elif stat.S_ISDIR(mode):
            out[str(p.relative_to(root))] = f"dir {stat.S_IMODE(mode):o}"
        else:
            out[str(p.relative_to(root))] = f"file {stat.S_IMODE(mode):o} {hashlib.sha256(p.read_bytes()).hexdigest()}"
    return out


def diff(a_image: Path, b_image: Path, work: Path, allow: list[str]) -> dict:
    a, b = work / "a", work / "b"
    unpack(a_image, a)
    unpack(b_image, b)
    ma, mb = manifest(a / "rootfs"), manifest(b / "rootfs")
    changed = sorted(k for k in ma.keys() | mb.keys() if ma.get(k) != mb.get(k))
    unexpected = [k for k in changed if not any(k.endswith(x) for x in allow)]
    meta_a, meta_b = (json.loads((x / "meta.json").read_text()) for x in (a, b))
    layout_equal = meta_a["squashfs"] == meta_b["squashfs"] and [m["name"] for m in meta_a["members"]] == [m["name"] for m in meta_b["members"]]
    kernel_identical = (a / "tar/var/tmp/kernel.image").read_bytes() == (b / "tar/var/tmp/kernel.image").read_bytes()
    shutil.rmtree(work)
    return {
        "files": len(ma),
        "changed": changed,
        "unexpected": unexpected,
        "layout_equal": layout_equal,
        "kernel_identical": kernel_identical,
        "ok": not unexpected and layout_equal and kernel_identical,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("unpack")
    p.add_argument("image", type=Path)
    p.add_argument("work", type=Path)
    p = sub.add_parser("repack")
    p.add_argument("work", type=Path)
    p.add_argument("out", type=Path)
    p = sub.add_parser("diff", help="prints a JSON report")
    p.add_argument("a", type=Path)
    p.add_argument("b", type=Path)
    p.add_argument("work", type=Path)
    p.add_argument("--allow", default="", help="comma separated path suffixes allowed to differ")
    a = ap.parse_args()
    if a.cmd == "unpack":
        unpack(a.image, a.work)
    elif a.cmd == "repack":
        repack(a.work, a.out)
    else:
        print(json.dumps(diff(a.a, a.b, a.work, [x for x in a.allow.split(",") if x]), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
