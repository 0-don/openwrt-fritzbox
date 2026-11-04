# WAV600 Wi-Fi on OpenWrt 6.18

Driver: Intel/MaxLinear `iwlwav` (`mtlk.ko`, `mtlkroot.ko`), GPL, from AVM's GPL archive and `maxlinear/iwlwav-dev`. Target: stock OpenWrt `mac80211` on the `intel-mips` xRX500 port (kernel 6.18).

MaxLinear builds this driver only against its own patched wireless stack (about 2,500 changed lines in mac80211, 1,700 in cfg80211, 430 in headers). OpenWrt ships one shared mac80211 for every device, so the port moves the vendor extensions into the driver and keeps stack patches to small generic ones.

## Wireless stack symbols missing in mainline 6.18

| Symbol | Vendor body | Driver calls | Plan |
|---|---|---|---|
| `ieee80211_tx_status` | 19 lines | 2 | rename: `ieee80211_tx_status_skb` |
| `ieee80211_color_switch_done` | 13 | 4 | rename: `ieee80211_color_change_finish` |
| `ieee80211_vif_to_name` | 14 | 65 (logging) | driver helper over `ieee80211_vif_to_wdev()->netdev` |
| `net_device_to_ieee80211_vif` | 10 | 2 | driver helper over `wdev_to_ieee80211_vif(dev->ieee80211_ptr)` |
| `cfg80211_get_cfg80211_wq` | 4 | 4 | driver workqueue, or `wiphy_work_queue` |
| `cfg80211_set/get_scan_expire_time` | 6 each | 3 each | drop (mainline fixed 30 s expiry) or small generic cfg80211 patch |
| `ieee80211_del_station` | 13 | 2 | disconnect through hostapd (`cfg80211_del_sta` notification) instead of calling mac80211 internals |
| `ieee80211_if_alloc` | 33 | 3 | create the master interface through `cfg80211` `add_virtual_intf` / mac80211's normal path |
| `ieee80211_get_obss_bsscolor_bitmap` | 13 | 2 | keep the bitmap in the driver; report with `cfg80211_obss_color_collision_notify` |
| `ieee80211_ml_chan_switch_notif_done` | 4 | 2 | Wi-Fi 7 multi-link only; compile out for WAV600 |
| `cfg80211_set_dfs_state` | 21 | 4 | generic `subsys` patch candidate (export), or report through `cfg80211_cac_event` |
| `cfg80211_set_dfs_state_bit_map` | | 2 | generic patch candidate: per-subchannel radar state |
| `ieee80211_radar_detected_rbm` | 40 | 3 | generic patch candidate on top of `ieee80211_radar_detected` (radar bitmap) |
| `ieee80211_dfs_cac_restart` | 15 | 2 | generic patch candidate, or restart CAC in the driver |

Result: 2 renames, 6 driver-local replacements, 1 drop, 5 DFS pieces that are generic features (partial-bandwidth radar handling) and the only candidates for stack patches.

## Private headers

`cfg80211.c`, `mac80211.c` and `wave_radio.c` include cfg80211's private `core.h`, `nl80211.h` and `reg.h`. The only private field use is `rdev->dfs_update_channels_wk`, in debug code. `get_cfg80211_regdom` and `get_wiphy_regdom` (cfg80211 internals) need public replacements (`wiphy->regd`, `reg_get_regdomain`-style accessors).

## nl80211 additions

MaxLinear's stack adds 2 commands (`SET_DFS_STATE`, `ML_CHAN_SWITCH_DONE_NOTIFY`) and 14 attributes not in 6.18 (DFS state, force, time, antenna, sub-band bandwidth, radar bitmap, color change timing, beacon color offset, RSSI, SNR, WDS, multi-link info). The driver itself uses none of them: they pass between cfg80211 and MaxLinear's hostapd. The driver already exposes 350 vendor subcommands, so these move to vendor commands (`NL80211_CMD_VENDOR`) and hostapd's DFS patches get rewritten against them.

## Known unknowns

- `fastpath=0` should take the driver off DirectConnect for the first bring-up (AVM sets `fastpath=1,1`). Not tested.
- Which of MaxLinear's 260 hostapd patches are required. Decided by the throwaway bring-up with the vendor stack.
- Firmware and calibration loading on OpenWrt (AVM loads both from TFFS).
