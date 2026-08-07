# jp-iptv-epg

A single, auto-refreshing TV guide (XMLTV/EPG) for the
[reaperc/jp-iptv](https://gitflic.ru/project/reaperc/jp-iptv) Japanese IPTV
playlist — built for players that load only one EPG URL (e.g. **UHF**).

## The one URL you need

Paste these into your player (paste **once** — they update themselves):

```
Playlist / M3U:  https://skinred78.github.io/jp-iptv-epg/jp-playlist.m3u
EPG source:      https://skinred78.github.io/jp-iptv-epg/jp-epg-merged.xml
```

Both served by GitHub Pages from the `dist` branch — the EPG as `application/xml`
(currently ~19 MB, gzip-compressed in transit), the playlist as `audio/x-mpegurl`.
The playlist is a mirror of the upstream `JP_Categories.m3u` with its `url-tvg`
header rewritten to the EPG above; we mirror it because gitflic (upstream) is
region-restricted/flaky from some networks and serves it via a query-string URL
with no `.m3u` extension.

jsDelivr was rejected as a host: it caps files at 20 MiB and caches force-pushed
branches for up to 12 h, breaking the twice-daily refresh (see Incidents below —
a player still pointed at a jsDelivr URL is the most likely reason the guide
looks stuck on old data).

## Why

Originally the playlist referenced four separate upstream EPG feeds, each
covering a different slice of channels (karenda, mathlabroom, akariko,
animenosekai), and several were awkward to load directly (a 20 MB raw file, a
15 MB release asset behind a redirect, a slow plain-HTTP feed). This repo merged
all four into one compact, well-formed file, aliasing the 8 Tokyo terrestrials
(`hdgd01`–`hdgd08`, which matched none of the four feeds) onto karenda's
Japanese-named channels.

That setup ended in **2026-07**, when `akariko.netgenx.site` stopped resolving
entirely — it was both an EPG source and the stream host for ~99 of the
playlist's channels, so this broke playback, not just the guide. Investigating
found upstream (reaperc) had already moved on: a new stream host, and a single
pre-merged EPG whose channel ids already match the playlist directly, so the
four-way merge and the terrestrial alias map are gone. The script's job now is
just: fetch that one upstream file, keep only the playlist's channels, trim to
fit under UHF's size ceiling, and re-host it on GitHub Pages.

Coverage as of the last rewrite: **157 of the playlist's 160 channels** get
listings (a few channels have no upstream EPG at all — normal, not a bug).

## How it stays fresh

`.github/workflows/build-epg.yml` runs `merge_epg.py` twice daily (16:00 & 04:00
JST) and force-pushes the result to the `dist` branch; GitHub Pages auto-redeploys
on push (~1 min). The daily commit also keeps the scheduled workflow from being
auto-disabled for inactivity.

Each run fetches the *live* playlist fresh (not a committed snapshot) and reads
the upstream EPG address from the playlist's own `url-tvg` header, rather than a
hardcoded URL — see Incidents below for why that matters. Two safety nets abort
the run (keeping the last-good published file) instead of overwriting it with
something broken:

- fewer than 130 of the playlist's channels come through — upstream likely
  changed channel ids or is serving a broken file.
- one programme title accounts for more than half of all listings — a strong
  signature of a placeholder/notice feed rather than a real guide (see below).

## Incidents

- **2026-08-07 — upstream served a "stolen copy" decoy.** Upstream rotates the
  EPG's address periodically (presumably to deter hardcoded, unauthorized
  mirrors); the old static path started returning valid-looking XML where every
  programme slot repeated the same anti-plagiarism notice instead of real
  listings. It passed the channel-count safety net (the decoy still declared
  the right channel ids), so it briefly published to the live guide. Fixed by
  reading the EPG address from the playlist's `url-tvg` header each run instead
  of a hardcoded URL, and added the repeated-title safety net above so the same
  failure shape gets caught automatically next time, whatever the wording.

## Maintenance

- **Run it now:** Actions tab → *Build JP IPTV EPG* → *Run workflow*.
- **Guide looks frozen for more than a day?** Check the latest Action run's log
  for one of the two safety-net `ERROR` lines above — that means upstream
  changed shape again and needs a look before the fix ships.
- **gitflic (playlist source) unreachable?** The build falls back to the
  committed `JP_Categories.m3u` snapshot automatically; refresh that file
  occasionally so the fallback doesn't drift too far from the live playlist.
