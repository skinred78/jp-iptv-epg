#!/usr/bin/env python3
"""Build a single, size-capped XMLTV EPG for the JP-IPTV playlist.

Why this exists
---------------
Originally this script merged FOUR separate upstream EPG feeds (karenda,
mathlabroom, akariko, animenosekai), because the playlist's own terrestrial
channel ids (hdgd01-08) matched none of them. That whole akariko-based setup
died in 2026-07: `akariko.netgenx.site` stopped resolving entirely — it was
both an EPG source AND the stream host for ~99 of 159 channels (all the
terrestrials), which is why playback broke, not just the guide.

Investigating the outage found the upstream jp-iptv project had *already*
fixed it: current playlists point streams at a new host and reference a single
pre-merged EPG (`jp-epg-26f0ce.gitlab.io`) whose channel ids already match the
playlist directly — no more alias hack needed. That upstream file is ~45 MB
uncompressed though, well past what UHF can load directly (see trim_to_fit),
so this script's remaining job is just: fetch it, keep only the playlist's
channels, trim to fit, and re-host on GitHub Pages in a format UHF accepts
(gzipped `application/xml`, newline-per-element, under ~20 MB).

What it does
------------
1. Fetches the live playlist once; derives which channel ids to keep AND
   mirrors the playlist itself (falls back to a committed snapshot if the
   upstream host is briefly down).
2. Fetches the upstream merged EPG, keeps only channels the playlist needs.
3. Trims to fit UHF's size ceiling, reformats one-element-per-line.
4. Refuses to publish a near-empty result (safety net if upstream breaks).
"""
import urllib.request
import gzip
import re
import sys
from datetime import datetime, timezone, timedelta
import xml.etree.ElementTree as ET

# UHF has to parse this on an Apple TV; keep the published file comfortably
# under a size that's proven to load.
SIZE_CAP = int(19.5 * 1024 * 1024)

# Upstream's own pre-merged EPG (channel ids already match the playlist).
EPG_SRC = "https://jp-epg-26f0ce.gitlab.io/guide.xml"

# Playlist mirror: gitflic (upstream host) is flaky/region-restricted from some
# networks and serves the file via a query-string URL with no .m3u extension, which
# some players reject. We mirror it to GitHub Pages with a clean .m3u URL and rewrite
# its EPG header to our trimmed feed.
PLAYLIST_SRC = "https://gitflic.ru/project/reaperc/jp-iptv/blob/raw?file=JP_Categories.m3u"
PLAYLIST_FALLBACK = "JP_Categories.m3u"  # committed snapshot, used if gitflic is down
PAGES_EPG_URL = "https://skinred78.github.io/jp-iptv-epg/jp-epg-merged.xml"


def fetch(url, tries=3, timeout=240):
    """Download a URL as text, retrying and transparently gunzipping."""
    last = None
    for _ in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "jp-iptv-epg-merger"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = r.read()
                encoded_gzip = r.info().get("Content-Encoding") == "gzip"
            if encoded_gzip or data[:2] == b"\x1f\x8b":
                data = gzip.decompress(data)
            return data.decode("utf-8", errors="replace")
        except Exception as e:  # noqa: BLE001 - network is allowed to flake
            last = e
    print(f"  WARN: failed to fetch {url}: {last}", file=sys.stderr)
    return None


def repaired_root(text):
    """Parse XMLTV text, repairing truncation by closing a dangling <tv>."""
    if text is None:
        return None
    if "</tv>" not in text:
        cut = max(text.rfind("</programme>"), text.rfind("</channel>"))
        if cut != -1:
            end = text.index(">", cut) + 1
            text = text[:end] + "\n</tv>"
    try:
        return ET.fromstring(text)
    except ET.ParseError as e:
        print(f"  WARN: parse error after repair: {e}", file=sys.stderr)
        return None


def load_playlist():
    """Fetch the live playlist once (falling back to the committed snapshot if
    upstream is briefly down), and write the Pages mirror with its EPG header
    rewritten to our trimmed feed. Returns the playlist text."""
    text = fetch(PLAYLIST_SRC)
    if text is None or "#EXTM3U" not in text:
        print("  WARN: playlist fetch failed; using committed fallback", file=sys.stderr)
        with open(PLAYLIST_FALLBACK, encoding="utf-8") as fh:
            text = fh.read()

    mirrored = re.sub(r'url-tvg="[^"]*"', f'url-tvg="{PAGES_EPG_URL}"', text, count=1)
    with open("jp-playlist.m3u", "w", encoding="utf-8") as fh:
        fh.write(mirrored)
    print(f"WROTE jp-playlist.m3u: {mirrored.count('#EXTINF')} channels (EPG header -> Pages)")
    return text


def trim_to_fit(merged):
    """Trim the merged guide so it stays under SIZE_CAP, keeping as many forward
    days as fit. Drops stale past programmes and bulky programme poster icons
    (channel logos come from the playlist, so they aren't needed here)."""
    channels = [e for e in merged if e.tag == "channel"]
    programmes = [e for e in merged if e.tag == "programme"]
    for pr in programmes:
        for ic in pr.findall("icon"):
            pr.remove(ic)

    jst = datetime.now(timezone.utc) + timedelta(hours=9)  # Japanese broadcast day
    lo = (jst - timedelta(days=1)).strftime("%Y%m%d")

    def build(hi):
        tv = ET.Element("tv", merged.attrib)
        for c in channels:
            tv.append(c)
        kept = 0
        for pr in programmes:
            if lo <= pr.get("start", "")[:8] <= hi:
                tv.append(pr)
                kept += 1
        return tv, kept

    # Take the largest forward window that fits; self-adapts to daily density.
    for fwd in range(8, 1, -1):
        hi = (jst + timedelta(days=fwd)).strftime("%Y%m%d")
        tv, kept = build(hi)
        size = len(ET.tostring(tv, encoding="utf-8"))
        if size <= SIZE_CAP:
            print(f"  window {lo}..+{fwd}d: {kept} programmes, {size/1e6:.1f} MB (fits)")
            return tv
        print(f"  window {lo}..+{fwd}d: {size/1e6:.1f} MB over cap, trying shorter")
    tv, kept = build((jst + timedelta(days=1)).strftime("%Y%m%d"))
    print(f"  fallback {lo}..+1d: {kept} programmes")
    return tv


def main():
    playlist_text = load_playlist()
    needed = set(re.findall(r'tvg-id="([^"]*)"', playlist_text))
    print(f"playlist references {len(needed)} channel ids")

    root = repaired_root(fetch(EPG_SRC))
    if root is None:
        print("ERROR: upstream EPG unavailable — refusing to overwrite the live EPG",
              file=sys.stderr)
        sys.exit(1)

    out = ET.Element("tv", {"generator-info-name": "jp-iptv merged EPG"})
    n_ch = n_pr = 0
    for ch in root.findall("channel"):
        if ch.get("id") in needed:
            out.append(ch)
            n_ch += 1
    for pr in root.findall("programme"):
        if pr.get("channel") in needed:
            out.append(pr)
            n_pr += 1
    print(f"merged (full): {n_ch} channels, {n_pr} programmes")

    # Safety net: this playlist has ~157 channels and the upstream EPG covers
    # ~155 of them directly. If far fewer show up, upstream likely changed
    # channel ids or is serving a broken file — abort so the Action keeps the
    # last-good published file rather than overwriting it with a bad one.
    if n_ch < 130:
        print(f"ERROR: only {n_ch} channels — upstream EPG or ids likely changed; "
              f"refusing to overwrite the live EPG", file=sys.stderr)
        sys.exit(1)

    final = trim_to_fit(out)

    # Serialize one element per line with a conventional XML declaration.
    # ElementTree's default output is a single multi-megabyte line, which some
    # player-side XML parsers (UHF included) reject; one element per line reads
    # like a normal XMLTV file and adds only ~30 KB of newlines.
    with open("jp-epg-merged.xml", "w", encoding="utf-8") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
        attrs = "".join(f' {k}="{v}"' for k, v in final.attrib.items())
        f.write(f"<tv{attrs}>\n")
        for el in final:
            f.write(ET.tostring(el, encoding="unicode"))
            f.write("\n")
        f.write("</tv>\n")
    print(f"WROTE jp-epg-merged.xml: {len(final.findall('channel'))} channels, "
          f"{len(final.findall('programme'))} programmes")


if __name__ == "__main__":
    main()
