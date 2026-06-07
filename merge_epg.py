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
import xml.etree.ElementTree as ET

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

    chans = len(out.findall("channel"))
    progs = len(out.findall("programme"))
    print(f"merged: {chans} channels, {progs} programmes")

    # Safety net: never publish a broken/near-empty guide.
    if chans < 50:
        print("ERROR: too few channels — refusing to overwrite the EPG", file=sys.stderr)
        sys.exit(1)

    ET.ElementTree(out).write("jp-epg-merged.xml", encoding="utf-8", xml_declaration=True)
    print("WROTE jp-epg-merged.xml")


if __name__ == "__main__":
    main()
