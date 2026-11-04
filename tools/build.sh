#!/usr/bin/env bash
# Builds the kernel and every kernel module package in an AVM GPL archive, matching the facts read from
# the official image. Called by ci/run.sh, which exports the variables checked below.
set -euo pipefail

HERE=$(cd "$(dirname "$0")/.." && pwd)
: "${W:?}" "${T:?}" "${ARCHIVE:?}" "${ROOTFS:?}" "${IMAGE_NAME:?}" "${GCC_VERSION:?}" "${DC_FLAGS?}" "${DC_MODE?}"
FREETZ=$W/freetz-ng
FREETZ_REV=${FREETZ_REV:-master}
JOBS=${JOBS:-$(nproc)}
SRC=$T/src
OUT=$T/out-modules
LOG=$T/build-logs
mkdir -p "$SRC" "$OUT" "$LOG"
facts() { python3 -I "$HERE/ci/facts.py" "$@"; }

# Kernel module packages only: userland trees carry a configure script, CMake or meson file.
if [ ! -f "$SRC/.extracted" ]; then
	python3 -I - "$ARCHIVE" > "$T/extract.list" <<'EOF'
import sys, tarfile
names = tarfile.open(sys.argv[1]).getnames()
top = {}
for n in names:
    p = n.removeprefix("./").split("/")
    if len(p) >= 2 and p[0] == "sources":
        top.setdefault(p[1], set()).add("/".join(p[2:]))
user = {"configure", "CMakeLists.txt", "meson.build", "configure.ac"}
print("./conf")
for d, files in sorted(top.items()):
    if d == "kernel" or (not files & user and any(f.split("/")[-1] in ("Kbuild", "Makefile") for f in files)):
        print(f"./sources/{d}")
EOF
	tar -xzf "$ARCHIVE" -C "$SRC" $(cat "$T/extract.list")
	touch "$SRC/.extracted"
fi

KSRC=$(dirname "$(grep -l '^VERSION = ' $(find "$SRC/sources/kernel" -maxdepth 3 -name Makefile) | head -1)")
KCONF=$(ls "$KSRC/.config.avm" "$SRC"/conf/kernel/*config* 2>/dev/null | head -1)
case "$(grep -E '^CONFIG_(MIPS|ARM64|ARM|X86)=y' "$KCONF" | head -1)" in
	CONFIG_MIPS=y) ARCH=mips ;;
	CONFIG_ARM64=y) ARCH=arm64 ;;
	CONFIG_ARM=y) ARCH=arm ;;
	CONFIG_X86=y) ARCH=x86 ;;
	*) echo "unknown architecture in $KCONF" >&2; exit 1 ;;
esac

# Kernel GCC from Freetz-NG: AVM's own prebuilt toolchain needs a token, Freetz builds the same version.
if [ ! -d "$FREETZ/.git" ]; then
	git init -q "$FREETZ"
	git -C "$FREETZ" fetch -q --depth 1 https://github.com/Freetz-NG/freetz-ng.git "$FREETZ_REV"
	git -C "$FREETZ" checkout -q FETCH_HEAD
fi
SYMBOLS=${FREETZ_SYMBOLS:-$(facts freetz "$FREETZ/config/mod/dl-firmware.in" "$IMAGE_NAME")}
printf '%s=y\n' $SYMBOLS > "$FREETZ/.config"
make -s -C "$FREETZ" olddefconfig >/dev/null
TC=$(ls -d "$FREETZ"/toolchain/build/*_gcc-"$GCC_VERSION"/*/bin-ccache/.. 2>/dev/null | head -1 || true)
if [ -z "$TC" ]; then
	make -C "$FREETZ" -j"$JOBS" kernel-toolchain > "$LOG/toolchain.log" 2>&1 || { tail -40 "$LOG/toolchain.log"; exit 1; }
	TC=$(ls -dt "$FREETZ"/toolchain/build/*_gcc-*/*/bin-ccache/.. | head -1)
fi
TC=$(realpath "$TC")/
# Freetz also installs short aliases like mips-linux-gcc; only the full triplet has binutils next to it.
for GCC in "$TC"bin-ccache/*-gcc; do
	CROSS=$(basename "${GCC%gcc}")
	[ -x "${TC}bin/${CROSS}ld" ] && break
done
BUILT_GCC=$("$GCC" -dumpversion)
[ "$BUILT_GCC" = "$GCC_VERSION" ] || echo "warning: AVM used GCC $GCC_VERSION, Freetz gave $BUILT_GCC" >&2
export PATH=$W/hostbin:${TC}bin-ccache:${TC}bin:$PATH
KMAKE=(ARCH=$ARCH CROSS_COMPILE=$CROSS HOSTCC=gcc)

# AVM's build directories, so __FILE__ strings in WARN/BUG messages come out byte for byte the same.
TREES=()
for d in "$SRC"/sources/*/; do
	d=${d%/}; [ "$d" = "$SRC/sources/kernel" ] && continue
	TREES+=("$(basename "$d" | tr -c 'A-Za-z0-9\n' _)=$d")
done
eval "$(facts prefixes "$ROOTFS" "$KSRC" "${TREES[@]}")"
map() { [ -n "${2:-}" ] && printf -- '-fmacro-prefix-map=%s/=%s/ ' "$1" "$2"; return 0; }

# The archive leaves the DirectConnect headers out of the kernel tree; out-of-tree users expect them there.
[ -d "$SRC/sources/directconnect_dp/include" ] && cp -n "$SRC"/sources/directconnect_dp/include/*.h "$KSRC/include/net/"

K=$T/kernel-build
mkdir -p "$K"
[ -f "$K/.config" ] || cp "$KCONF" "$K/.config"
make -C "$KSRC" O="$K" "${KMAKE[@]}" olddefconfig > "$LOG/kernel.log" 2>&1
make -C "$KSRC" O="$K" "${KMAKE[@]}" KCFLAGS="$(map "$KSRC" "${PREFIX_KERNEL:-}")" -j"$JOBS" vmlinux modules >> "$LOG/kernel.log" 2>&1 \
	|| { tail -40 "$LOG/kernel.log"; exit 1; }
cp "$K/Module.symvers" "$T/Module.symvers"

# AVM's published nl80211.c lost the first line of two #if blocks (7590 AX 08.2x); a no-op elsewhere.
fixups() {
	local f=$1/net/wireless/nl80211.c
	[ -f "$f" ] || return 0
	python3 -I - "$f" <<'EOF'
import re, sys
p = sys.argv[1]
s = open(p).read()
s2, n = re.subn(r'\n \\\n(\tLINUX_VERSION_CODE >= KERNEL_VERSION\(\d+, \d+, 0\) \|\| \\\n)', r'\n#if \\\n\1', s)
if n:
    open(p, "w").write(s2)
EOF
}

declare -A RESULT
for entry in "${TREES[@]}"; do
	key=${entry%%=*}; dir=${entry#*=}; name=$(basename "$dir")
	pfx=PREFIX_${key^^}; hdr=HEADERS_${key^^}
	flags="$(map "$dir" "${!pfx:-}")$(map "$KSRC" "${!hdr:-}")"
	if [ -d "$dir/src/kconf" ] || [ -d "$dir/kconf" ]; then
		# backports tree (cfg80211, mac80211 and vendor Wi-Fi drivers)
		bp=$( [ -d "$dir/src/kconf" ] && echo "$dir/src" || echo "$dir" )
		fixups "$bp"
		if [ ! -x "$W/hostbin/conf" ]; then
			make -s -C "$bp/kconf" conf CC=gcc CFLAGS='-O2 -std=gnu11' && mkdir -p "$W/hostbin" && ln -sf "$bp/kconf/conf" "$W/hostbin/conf"
		fi
		facts backports "$ROOTFS" "$bp" > "$bp/defconfig"
		# No CC=: iwlwav wraps CC with mtlkfront.sh, a command line CC would bypass it.
		cmd=(make -C "$bp" KLIB_BUILD="$K" KLIB="$K" ARCH=$ARCH CROSS_COMPILE=$CROSS)
		{ "${cmd[@]}" usedefconfig && "${cmd[@]}" KCFLAGS="$flags" -j"$JOBS" modules; } > "$LOG/$name.log" 2>&1 \
			&& RESULT[$name]=ok || RESULT[$name]=failed
		continue
	fi
	extra=()
	if [ "$name" = directconnect_dp ]; then
		flags+="-I$dir/include $DC_FLAGS"
		extra=("CONFIG_DIRECTCONNECT_DP_$DC_MODE=y")
	fi
	# Shallowest directory with kbuild rules; trees without any are userland and get skipped.
	m=$(grep -rlsE '^\s*obj-(m|\$\()' --include=Kbuild --include=Makefile "$dir" | xargs -r -n1 dirname | awk '{print length, $0}' | sort -n | head -1 | cut -d' ' -f2- || true)
	[ -n "$m" ] || continue
	make -C "$KSRC" O="$K" "${KMAKE[@]}" M="$m" "${extra[@]}" KCFLAGS="$flags" -j"$JOBS" modules > "$LOG/$name.log" 2>&1 \
		&& RESULT[$name]=ok || RESULT[$name]=failed
done

rm -f "$OUT"/*.ko
find "$K" "$SRC/sources" -name '*.ko' | while read -r ko; do
	"${TC}bin/${CROSS}strip" --strip-debug -o "$OUT/$(basename "$ko")" "$ko"
done
{
	echo "| package | result |"; echo "|---|---|"
	for k in "${!RESULT[@]}"; do echo "| $k | ${RESULT[$k]} |"; done | sort
} > "$T/packages.md"
echo "kernel $(cat "$K/include/config/kernel.release"), $(ls "$OUT" | wc -l) modules in $OUT"
