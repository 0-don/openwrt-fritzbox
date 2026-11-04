# openwrt-fritzbox

OpenWrt for FRITZ!Box, built on a kernel and drivers rebuilt from AVM's published GPL sources.

## Why not Freetz-NG

Freetz-NG adds packages next to AVM's closed firmware. Its own code shows where that ends:

- Kernel replacement is switched off (`FREETZ_REPLACE_KERNEL_DISABLED` in `config/mod/source.in`) for GRX5, IPQ40xx, Alder, Maple, PUMA7 and every box on FRITZ!OS 7.1x or newer except VR9 and PUMA6, so on practically every current FRITZ!Box the kernel and drivers stay AVM's binaries.
- Extra kernel modules are built without checking them against AVM's kernel; its help text warns "Unknown symbols are possible due to avm's hacks".
- Every FRITZ!OS release needs a hand written board entry before it builds, and its prebuilt toolchain needs a private token.

This project builds the kernel and every GPL module itself and proves each one against AVM's binary (vermagic, exports, every modversions CRC), resolves model and version at runtime from AVM's servers, builds the matching GCC itself, and tests every published model daily.

## Road to OpenWrt

AVM's kernel ships without connection tracking, iptables or nftables: NAT and firewalling live in AVM's packet accelerator and userland. OpenWrt therefore needs our own kernel build with netfilter enabled, plus OpenWrt's userland (procd, netifd, uci, LuCI, hostapd, fw3 on the 4.9 kernel). The Wi-Fi driver is mac80211 based, so OpenWrt's hostapd drives it. Closed and out of reach: DSL (`kdsldmod`), DECT and telephony.

Done: kernel and GPL modules rebuilt and proven (62 of 94 modules on the 7590 AX, FRITZ!OS 08.25), firmware unpack and repack proven file for file.

## Usage

```sh
ci/run.sh fritzbox-7590-ax           # latest FRITZ!OS
ci/run.sh fritzbox-7590-ax 08.25     # a specific version
python3 ci/resolve.py --list         # every model that can be built
```

Host needs: Freetz-NG prerequisites (`tools/prerequisites install`), squashfs-tools, kmod. The result lands in `~/.cache/fritz-build/<model>-<version>/`: `report/summary.md` and the repacked image.

Or run the `firmware proof` workflow from your own fork. The `daily validation` workflow builds every model on x64 and arm64 runners and uploads nothing.

## What the proof checks

Per module: same vermagic, same dependencies, identical exported symbols, every import resolvable against our kernel and AVM's other modules, every modversions CRC equal to AVM's, and the loaded sections within 0.5% of AVM's size. Only modules passing all checks are swapped in.

Per image: repacking the untouched image changes no file, squashfs parameters and tar layout are equal, and after the swap only the swapped modules differ while `kernel.image` stays untouched.

Result for the FRITZ!Box 7590 AX, FRITZ!OS 08.25: 62 of 94 modules rebuilt and proven; the rest are AVM proprietary or not yet buildable from the archive.

## Legal

This repository contains only original scripts. It never ships AVM firmware, AVM binaries or anything derived from them: every user downloads the official image from AVM and builds for their own device. Do not share a repacked image; it contains AVM software (UrhG 69c). The repacked image is not signed by AVM, so the web interface refuses it; flashing goes through the bootloader (Freetz-NG `tools/push_firmware`) at your own risk.

Not affiliated with AVM. FRITZ!Box and FRITZ!OS are trademarks of AVM GmbH.
