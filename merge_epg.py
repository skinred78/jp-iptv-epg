#!/usr/bin/env python3
"""Build a single merged XMLTV EPG for the JP-IPTV playlist.

Why this exists
---------------
The jp-iptv playlist (reaperc/jp-iptv on gitflic) references FOUR separate EPG
feeds, each covering a disjoint slice of the channels. Most IPTV players choke
when asked to load all four directly:
  * karenda is a 20 MB raw github file (octet-stream)  -> too big / wrong type
  * mathlabroom is a 15 MB github RELEASE asset        -> redirect + octet-stream
  * akariko is plain HTTP and slow (~28 s, sometimes truncated)
  * players that load only one URL, or abort on a single failed source, fail

This script does the heavy lifting server-side (in GitHub Actions, where size
and slow HTTP don't matter), then publishes ONE compact, well-formed XMLTV file
that any player can load as a single URL.

What it does
------------
1. Downloads all four upstream feeds (with retries; gzip-aware).
2. Repairs truncated feeds (closes a dangling <tv> at the last complete element).
3. Keeps only the channels referenced by the playlist (JP.m3u) -> keeps it lean.
4. Aliases the 8 Tokyo terrestrials (hdgd01-08, which match NO upstream id) onto
   karenda's Japanese-named channels, so the *unmodified* playlist gets a guide
   for NHK/NTV/TBS/Fuji/etc. with no per-channel mapping needed.
5. Writes jp-epg-merged.xml and refuses to publish a near-empty file.
"""
import urllib.request
import gzip
import re
import copy
import sys
from datetime import datetime, timezone, timedelta
import xml.etree.ElementTree as ET

# jsDelivr rejects GitHub files over 20 MiB, and UHF has to parse this on an
# Apple TV, so we keep the published file comfortably under this cap.
SIZE_CAP = int(19.5 * 1024 * 1024)

# Upstream feeds. karenda via jsDelivr (compact, gzipped) just for speed; the
# others are fetched at full size — fine in CI.
SOURCES = {
    "karenda":      "https://cdn.jsdelivr.net/gh/karenda-jp/etc@main/guides.xml",
    "mathlabroom":  "https://github.com/mathlabroom/SKyperfectv-EPG-/releases/download/latest/epg_ultimate.xml",
    "akariko":      "http://akariko.netgenx.site/epg/kai-epg.xml",
    "animenosekai": "https://animenosekai.github.io/japanterebi-xmltv/guide.xml",
}

PLAYLIST = "JP.m3u"  # committed snapshot; defines which channels to keep

# Tokyo terrestrials: playlist tvg-id -> karenda channel id to borrow programmes from
ALIAS = {
    "hdgd01": "NHK東京・総合_jp",
    "hdgd02": "NHK東京・教育_jp",
    "hdgd03": "日本テレビ_jp",
    "hdgd04": "TBS_jp",
    "hdgd05": "フジテレビ_jp",
    "hdgd06": "テレビ朝日_jp",
    "hdgd07": "テレ東_jp",
    "hdgd08": "TOKYO・MX_jp",
}


def fetch(url, tries=3):
    """Download a URL as text, retrying and transparently gunzipping."""
    last = None
    for _ in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "jp-iptv-epg-merger"})
            with urllib.request.urlopen(req, timeout=180) as r:
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
    with open(PLAYLIST, encoding="utf-8") as fh:
        needed = set(re.findall(r'tvg-id="([^"]*)"', fh.read()))
    print(f"playlist references {len(needed)} channel ids")

    out = ET.Element("tv", {"generator-info-name": "jp-iptv merged EPG"})
    seen = set()
    karenda_root = None

    for name, url in SOURCES.items():
        root = repaired_root(fetch(url))
        if root is None:
            print(f"  skip {name} (unavailable)", file=sys.stderr)
            continue
        if name == "karenda":
            karenda_root = root
        n_ch = n_pr = 0
        for ch in root.findall("channel"):
            cid = ch.get("id")
            if cid in needed and cid not in seen:
                out.append(ch)
                seen.add(cid)
                n_ch += 1
        for pr in root.findall("programme"):
            if pr.get("channel") in needed:
                out.append(pr)
                n_pr += 1
        print(f"  {name}: +{n_ch} channels, +{n_pr} programmes")

    # Terrestrial aliases: copy karenda's terrestrial channel + programmes under
    # the playlist's hdgd0X ids so the unmodified playlist gets a guide.
    if karenda_root is not None:
        kch = {c.get("id"): c for c in karenda_root.findall("channel")}
        kprog = {}
        for pr in karenda_root.findall("programme"):
            kprog.setdefault(pr.get("channel"), []).append(pr)
        for hd, tgt in ALIAS.items():
            if tgt in kch and hd not in seen:
                c = copy.deepcopy(kch[tgt])
                c.set("id", hd)
                out.append(c)
                seen.add(hd)
            for pr in kprog.get(tgt, []):
                p = copy.deepcopy(pr)
                p.set("channel", hd)
                out.append(p)
    else:
        print("  WARN: karenda unavailable — terrestrial aliases skipped", file=sys.stderr)

    print(f"merged (full): {len(out.findall('channel'))} channels, {len(out.findall('programme'))} programmes")

    # Safety net: never publish a broken/near-empty guide.
    if len(out.findall("channel")) < 50:
        print("ERROR: too few channels — refusing to overwrite the EPG", file=sys.stderr)
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
