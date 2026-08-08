#!/usr/bin/env python3
"""Repair pass: for schools with gaps, discover working stats URLs by probing
the live sites, refetch those seasons, and rewrite the stats/gaps CSVs.

Reads:  d3_baseball_stats_urls.csv, d3_batting_stats.csv, d3_batting_gaps.csv
Writes: d3_batting_stats.csv, d3_batting_gaps.csv (updated in place),
        d3_baseball_stats_urls.csv (repaired URLs), repair_report.txt
"""
import csv
import re
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

import requests

from scrape_d3_batting import SEASONS, STAT_COLS, UA, extract

TIMEOUT = 25


def get(url):
    try:
        r = requests.get(url, headers=UA, timeout=TIMEOUT)
        return r
    except requests.RequestException:
        return None


def canonical_domain(domain):
    """Follow the homepage redirect to the site's real host."""
    for scheme_host in (f"https://{domain}", f"https://www.{domain.removeprefix('www.')}"):
        r = get(scheme_host)
        if r is not None and r.status_code == 200:
            return urlparse(r.url).netloc
    return None


def presto_slugs(host, season):
    yy = f"{int(season)-1}-{season[2:]}"
    r = get(f"https://{host}/sports/bsb/{yy}/teams")
    if r is None or r.status_code != 200:
        return []
    return list(dict.fromkeys(re.findall(r"/sports/bsb/" + re.escape(yy) + r"/teams/([a-z0-9_.-]+)", r.text, re.I)))


def try_url(url):
    r = get(url)
    if r is None or r.status_code != 200:
        return None
    return extract(r.text)


def find_pattern(school, domain):
    """Return (kind, host, slug) able to produce a batting table, or None.
    kind 'sidearm' -> https://host/sports/baseball/stats/{Y}
    kind 'presto'  -> https://host/sports/bsb/{Y-1}-{yy}/teams/{slug}
    """
    hosts = []
    for d in (domain, canonical_domain(domain)):
        if d and d not in hosts:
            hosts.append(d)
    probe = "2025"
    for host in hosts:
        for path in (f"https://{host}/sports/baseball/stats/{probe}",
                     f"https://{host}/sports/base/stats/{probe}",
                     f"https://{host}/sports/bsb/stats/{probe}"):
            t = try_url(path)
            if t is not None and len(t) >= 5:
                return ("sidearm", host, path.split("/sports/")[1].split("/stats")[0])
        slugs = presto_slugs(host, probe)
        toks = set(re.findall(r"[a-z]+", school.lower())) - {"university", "college", "of", "the"}
        slugs.sort(key=lambda s: -sum(tok[:6] in s.lower() for tok in toks))
        for slug in slugs[:3]:
            t = try_url(season_url("presto", host, slug, probe))
            if t is not None and len(t) >= 5:
                return ("presto", host, slug)
    return None


PRESTO_TMPL = "?tmpl=teaminfo-network-monospace-template&sort=ab&pos=h"


def season_url(kind, host, slug, season):
    if kind == "sidearm":
        return f"https://{host}/sports/{slug}/stats/{season}"
    yy = f"{int(season)-1}-{season[2:]}"
    return f"https://{host}/sports/bsb/{yy}/teams/{slug}{PRESTO_TMPL}"


def repair_school(args):
    school, conf, domain, seasons = args
    found = find_pattern(school, domain)
    stats, still = [], []
    if found is None:
        return school, None, [], [(s, "unresolved", "no working URL pattern found on live site") for s in seasons]
    kind, host, slug = found
    for season in seasons:
        url = season_url(kind, host, slug, season)
        t = try_url(url)
        if t is None or t.empty:
            still.append((season, "http_or_parse", f"pattern found but {season} page missing/empty: {url}"))
        else:
            for _, p in t.iterrows():
                stats.append([school, conf, season, p.get("player", "")] +
                             [p.get(c, "") for c in STAT_COLS])
        time.sleep(0.2)
    return school, (kind, host, slug), stats, still


def main():
    with open("d3_baseball_stats_urls.csv", newline="") as f:
        url_rows = list(csv.DictReader(f))
    by_school = {r["school"]: r for r in url_rows}

    with open("d3_batting_gaps.csv", newline="") as f:
        gaps = list(csv.DictReader(f))
    fixable = {}
    keep_gaps = []
    for g in gaps:
        if g["status"] == "no_url":
            keep_gaps.append(g)
        else:
            fixable.setdefault(g["school"], []).append(g["season"])

    jobs = []
    for school, seasons in fixable.items():
        r = by_school[school]
        jobs.append((school, r["conference"], r["athletics_domain"], seasons))
    print(f"repairing {len(jobs)} schools / {sum(len(j[3]) for j in jobs)} school-seasons")

    new_stats, report = [], []
    with ThreadPoolExecutor(max_workers=10) as ex:
        for school, found, stats, still in ex.map(repair_school, jobs):
            new_stats.extend(stats)
            if found:
                kind, host, slug = found
                report.append(f"FIXED {school}: {kind} on {host} (slug={slug}), +{len(stats)} rows, {len(still)} seasons still missing")
                row = by_school[school]
                row["platform"] = kind
                row["athletics_domain"] = host
                for season in SEASONS:
                    if row.get(f"url_{season}", "").strip():
                        row[f"url_{season}"] = season_url(kind, host, slug, season)
            else:
                report.append(f"UNRESOLVED {school}: no working pattern, {len(still)} seasons missing")
            for season, status, reason in still:
                keep_gaps.append({"school": school, "season": season,
                                  "url": by_school[school].get(f"url_{season}", ""),
                                  "status": status, "reason": reason})
            print(report[-1], flush=True)

    # Append repaired stats
    with open("d3_batting_stats.csv", "a", newline="") as f:
        csv.writer(f).writerows(new_stats)
    # Rewrite gaps: kept + still-missing
    with open("d3_batting_gaps.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["school", "season", "url", "status", "reason"])
        w.writeheader()
        w.writerows(sorted(keep_gaps, key=lambda g: (g["school"], g["season"])))
    # Rewrite URL map with fixes
    with open("d3_baseball_stats_urls.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(url_rows[0].keys()))
        w.writeheader()
        w.writerows(url_rows)
    with open("repair_report.txt", "w") as f:
        f.write("\n".join(report) + "\n")
    print(f"repair done: +{len(new_stats)} player-seasons, {len(keep_gaps)} gaps remain")


if __name__ == "__main__":
    main()
