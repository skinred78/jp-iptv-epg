# jp-iptv-epg

A single, auto-refreshing TV guide (XMLTV/EPG) for the
[reaperc/jp-iptv](https://gitflic.ru/project/reaperc/jp-iptv) Japanese IPTV
playlist — built for players that load only one EPG URL (e.g. **UHF**).

## The one URL you need

Paste this into your player's EPG source field (paste it **once** — it updates itself):

```
https://skinred78.github.io/jp-iptv-epg/jp-epg-merged.xml
```

Served by GitHub Pages from the `dist` branch as gzipped `application/xml`
(~4 MB over the wire). jsDelivr was rejected as a host: it caps files at 20 MiB
and caches force-pushed branches for up to 12 h, which breaks daily refresh.

## Why

The playlist references four separate EPG feeds, each covering a *different*
slice of channels, and several are awkward to load directly (a 20 MB raw file, a
15 MB release asset behind a redirect, a slow plain-HTTP feed). Many players load
only the first URL, or abort if any one feed fails — so the guide ends up mostly
empty.

This repo merges all four into one compact, well-formed file:

| Source | Covers | Notes |
|---|---|---|
| karenda | terrestrial / BS / CS / Rakuten / FAST (~76) | main feed |
| mathlabroom | SkyPerfecTV BS/CS (~56) | github release asset |
| akariko | BS / satellite (~23) | plain HTTP, slow |
| animenosekai | NHK World + international (~3) | |

Coverage: **158 / 159** playlist channels (only `rch_61` / しまじろうチャンネル has
no EPG anywhere). The 8 Tokyo terrestrials (`hdgd01`–`hdgd08`), whose playlist
tvg-ids match no upstream feed, are **aliased** onto karenda's Japanese-named
channels — so the playlist works unmodified.

## How it stays fresh

`.github/workflows/build-epg.yml` runs `merge_epg.py` twice daily (16:00 & 04:00
JST) and force-pushes the result to the `dist` branch; GitHub Pages auto-redeploys
on push (~1 min). The daily commit also keeps the scheduled workflow from being
auto-disabled for inactivity.

## Maintenance

- **Playlist changed / new channels?** Replace `JP.m3u` with the latest and
  commit — the next build picks up the new channel ids.
- **Run it now:** Actions tab → *Build JP IPTV EPG* → *Run workflow*.
- **A terrestrial id changed upstream?** Edit the `ALIAS` map in `merge_epg.py`.
