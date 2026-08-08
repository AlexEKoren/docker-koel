#!/usr/bin/env python3
"""Final refetch: pull remaining gap seasons using agent-verified URL patterns.

Reads batch files of school|kind|host|slug|verified lines, refetches all
missing seasons, with a per-season conference-site fallback (e.g. Anna Maria's
2022-2025 live on the GNAC site but 2026 on the MASCAC site).
"""
import csv
import glob
import re
import sys
import time

from cleanup_conf import CONF_HOSTS, best_slug, conf_slugs
from repair_gaps import season_url, try_url
from scrape_d3_batting import STAT_COLS

BATCH_GLOB = sys.argv[1] if len(sys.argv) > 1 else "/tmp/claude-0/-home-user-docker-koel/397cbac9-2f72-52c2-aee0-d34a5fb0e5a4/scratchpad/final_urls_batch*.txt"


def norm(s):
    return re.sub(r"[^a-z]", "", s.lower().replace("university", "").replace("college", ""))


def main():
    resolved = {}
    for path in glob.glob(BATCH_GLOB):
        for ln in open(path):
            ln = ln.split("#")[0].strip()
            if not ln:
                continue
            school, kind, host, slug, verified = [p.strip() for p in ln.split("|")]
            resolved[norm(school)] = (kind, host, slug, verified == "yes")

    with open("d3_baseball_stats_urls.csv", newline="") as f:
        url_rows = list(csv.DictReader(f))
    by_school = {r["school"]: r for r in url_rows}

    with open("d3_batting_gaps.csv", newline="") as f:
        gaps = list(csv.DictReader(f))

    # Drop UNE entirely (no varsity baseball; removed from roster)
    url_rows = [r for r in url_rows if r["school"] != "University of New England"]
    gaps = [g for g in gaps if g["school"] != "University of New England"]

    fixable, keep = {}, []
    for g in gaps:
        if g["status"] == "no_url":
            keep.append(g)
        else:
            fixable.setdefault(g["school"], []).append(g["season"])

    new_stats = []
    for school, seasons in sorted(fixable.items()):
        row = by_school[school]
        entry = resolved.get(norm(school))
        if not entry or entry[0] == "none" or not entry[3]:
            reason = "school site serves HTTP 500 for baseball stats (site-side outage)" \
                if entry and entry[3] is False else "no working source found"
            for season in seasons:
                keep.append({"school": school, "season": season,
                             "url": row.get(f"url_{season}", ""),
                             "status": "unavailable", "reason": reason})
            print(f"GAP {school}: {reason}", flush=True)
            continue
        kind, host, slug, _ = entry
        got = []
        for season in seasons:
            url = season_url(kind, host, slug, season)
            t = try_url(url)
            if (t is None or t.empty) and row["conference"] in CONF_HOSTS:
                chost = CONF_HOSTS[row["conference"]]
                cslug = best_slug(school, conf_slugs(chost, season))
                if cslug:
                    alt = season_url("presto", chost, cslug, season)
                    t2 = try_url(alt)
                    if t2 is not None and not t2.empty:
                        t, url = t2, alt
            if t is not None and not t.empty:
                for _, p in t.iterrows():
                    new_stats.append([school, row["conference"], season,
                                      p.get("player", "")] +
                                     [p.get(c, "") for c in STAT_COLS])
                row[f"url_{season}"] = url
                got.append(season)
            else:
                keep.append({"school": school, "season": season, "url": url,
                             "status": "season_missing",
                             "reason": "verified source lacks this season"})
            time.sleep(0.15)
        row["platform"] = kind
        print(f"{school}: recovered {got} of {seasons}", flush=True)

    with open("d3_batting_stats.csv", "a", newline="") as f:
        csv.writer(f).writerows(new_stats)
    with open("d3_batting_gaps.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["school", "season", "url", "status", "reason"])
        w.writeheader()
        w.writerows(sorted(keep, key=lambda g: (g["school"], g["season"])))
    with open("d3_baseball_stats_urls.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(url_rows[0].keys()))
        w.writeheader()
        w.writerows(url_rows)
    print(f"final refetch done: +{len(new_stats)} rows, {len(keep)} gaps remain")


if __name__ == "__main__":
    main()
