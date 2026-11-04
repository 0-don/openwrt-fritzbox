#!/usr/bin/env bash
# Usage: ci/run.sh <model> [version]   for example: ci/run.sh fritzbox-7590-ax
# Fetches the official image and GPL archive from AVM, rebuilds every GPL module, proves each one
# against AVM's binary, swaps in the proven ones and repacks. Everything AVM made stays on this machine.
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
export W=${W:-$HOME/.cache/fritz-build}
eval "$(python3 -I "$HERE/resolve.py" "${1:?usage: $0 <model> [version]}" --version "${2:-latest}" --env)"
export T=$W/$MODEL-$VERSION
R=$T/report
mkdir -p "$T/dl" "$R"

for url in "$IMAGE_URL" "$ARCHIVE_URL"; do
	f=$T/dl/$(basename "$url")
	[ -s "$f" ] || { curl -fL --retry 3 -o "$f.part" "$url" && mv "$f.part" "$f"; }
done
export IMAGE_NAME ARCHIVE=$T/dl/$ARCHIVE_NAME ROOTFS=$T/fw/rootfs

python3 -I "$HERE/fwpack.py" unpack "$T/dl/$IMAGE_NAME" "$T/fw"
set -a
eval "$(python3 -I "$HERE/facts.py" image "$ROOTFS")"
set +a
"$HERE/../tools/build.sh"

python3 -I "$HERE/verify_modules.py" --ours "$T/out-modules" --avm "$ROOTFS/lib/modules/$KREL" \
	--symvers "$T/Module.symvers" --json "$R/modules.json" --markdown "$R/modules.md" --passed "$T/passed.txt" >/dev/null

# An untouched repack must reproduce every file, otherwise the swap proof below proves nothing.
python3 -I "$HERE/fwpack.py" repack "$T/fw" "$T/roundtrip.image"
python3 -I "$HERE/fwpack.py" diff "$T/dl/$IMAGE_NAME" "$T/roundtrip.image" "$T/diff" > "$R/roundtrip.json"
rm "$T/roundtrip.image"

ALLOW=
while read -r avm; do
	cp "$T/out-modules/$(basename "$avm")" "$avm"
	ALLOW+=$(basename "$avm"),
done < "$T/passed.txt"
OUT_IMAGE=$T/${IMAGE_NAME%.image}-gpl-rebuilt.image
python3 -I "$HERE/fwpack.py" repack "$T/fw" "$OUT_IMAGE"
python3 -I "$HERE/fwpack.py" diff "$T/dl/$IMAGE_NAME" "$OUT_IMAGE" "$T/diff" --allow "$ALLOW" > "$R/swap.json"

python3 -I - "$R" "$T/packages.md" <<EOF
import json, sys
from pathlib import Path
r = Path(sys.argv[1])
rt, sw, mods = (json.loads((r / f).read_text()) for f in ("roundtrip.json", "swap.json", "modules.json"))
ok = rt["ok"] and sw["ok"] and mods["ok"]
head = f"""# $MODEL FRITZ!OS $VERSION: {'PASS' if ok else 'FAIL'}

| fact | value |
|---|---|
| image | $IMAGE_NAME, sha256 $(sha256sum "$T/dl/$IMAGE_NAME" | cut -d' ' -f1) |
| GPL archive | $ARCHIVE_NAME, sha256 $(sha256sum "$ARCHIVE" | cut -d' ' -f1) |
| kernel | $KREL, GCC $GCC_VERSION |
| untouched repack, files changed | {len(rt['changed'])} of {rt['files']} |
| squashfs parameters and tar layout equal | {rt['layout_equal']} |
| modules swapped | {len(sw['changed'])}, all expected: {not sw['unexpected']} |
| kernel.image untouched | {sw['kernel_identical']} |
"""
(r / "summary.md").write_text("\n".join([head, "## Packages\n", Path(sys.argv[2]).read_text(), (r / "modules.md").read_text()]))
print(head)
sys.exit(0 if ok else 1)
EOF
echo "image for your own box (do not share it, it contains AVM's software): $OUT_IMAGE"
