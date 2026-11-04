# fritzbox-gpl-rebuild

Rebuilds the Linux kernel and every GPL kernel module of a FRITZ!Box from AVM's published GPL sources, proves each module is a drop-in replacement for AVM's binary, and repacks the official firmware with the proven modules swapped in.

Everything is resolved at runtime: the latest image and matching GPL archive from AVM's servers, the kernel release and GCC version from the image, AVM's build directories (so `__FILE__` strings match byte for byte), the backports and DirectConnect configuration, and the Freetz-NG board type for the toolchain.

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
