#!/usr/bin/env python3
"""Final cleanup: fetch unresolved schools' stats via their conference's
presto site (per-team pages with all slugs), when the school's own site is
blocked or wrong.

Updates d3_batting_stats.csv, d3_batting_gaps.csv, d3_baseball_stats_urls.csv.
"""
import csv
import re
import time

import requests

from repair_gaps import PRESTO_TMPL, season_url, try_url
from scrape_d3_batting import SEASONS, STAT_COLS, UA

CONF_HOSTS = {
    "MASCAC": "www.mascac.com",
    "GNAC": "www.thegnac.com",
    "Conference of New England": "cnesports.org",
    "AMCC": "www.amccsports.org",
    "SCAC": "www.scacsports.com",
    "Empire 8": "empire8.com",
    "SLIAC": "sliac.org",
    "HCAC": "heartlandconf.org",
    "USA South": "usasouth.net",
    "Midwest Conference": "midwestconference.org",
    "Collegiate Conference of the South": "collegiateconferenceofthesouth.com",
    "American Southwest Conference": "ascsports.org",
    "UMAC": "umacathletics.com",
    "MIAC": "www.miacathletics.com",
    "Presidents' Athletic Conference": "pacathletics.org",
    "Landmark": "landmarkconference.org",
    "OAC": "oac.org",
    "NCAC": "northcoast.org",
    "American Rivers Conference": "rollrivers.com",
    "NJAC": "njacsports.com",
    "Skyline": "skylineconference.org",
    "Atlantic East": "atlanticeast.com",
    "United East": "gounitedeast.com",
    "Coast to Coast": "www.c2csports.com",
    "North Atlantic Conference": "nacathletics.com",
    "NEWMAC": "newmacsports.com",
    "CCIW": "cciw.org",
    "MIAA": "miaa.org",
    "NACC": "naccsports.org",
    "Northwest Conference": "nwcsports.com",
    "Centennial": "centennial.org",
    "MAC Freedom": "gomacsports.com",
    "MAC Commonwealth": "gomacsports.com",
    "ODAC": "odaconline.com",
    "SAA": "saa-sports.com",
    "SUNYAC": "www.sunyacsports.com",
    "Liberty League": "libertyleagueathletics.com",
    "CUNYAC": "cunyathletics.com",
    "Little East": "littleeast.com",
    "WIAC": "wiacsports.com",
    "NESCAC": "nescac.com",
}

slug_cache = {}


def conf_slugs(host, season):
    key = (host, season)
    if key not in slug_cache:
        yy = f"{int(season)-1}-{season[2:]}"
        try:
            r = requests.get(f"https://{host}/sports/bsb/{yy}/teams", headers=UA, timeout=25)
            slugs = list(dict.fromkeys(re.findall(
                r"/sports/bsb/" + re.escape(yy) + r"/teams/([a-z0-9_.-]+)", r.text, re.I))) \
                if r.status_code == 200 else []
        except requests.RequestException:
            slugs = []
        slug_cache[key] = slugs
    return slug_cache[key]


def best_slug(school, slugs):
    toks = [t for t in re.findall(r"[a-z]+", school.lower())
            if t not in {"university", "college", "of", "the", "at", "saint", "st"}]
    scored = sorted(slugs, key=lambda s: -sum(t[:6] in s.lower() for t in toks))
    if scored and sum(t[:6] in scored[0].lower() for t in toks):
        return scored[0]
    return None


def main():
    with open("d3_baseball_stats_urls.csv", newline="") as f:
        url_rows = list(csv.DictReader(f))
    by_school = {r["school"]: r for r in url_rows}

    with open("d3_batting_gaps.csv", newline="") as f:
        gaps = list(csv.DictReader(f))
    fixable = {}
    keep = []
    for g in gaps:
        if g["status"] == "no_url":
            keep.append(g)
        else:
            fixable.setdefault(g["school"], []).append(g["season"])

    new_stats = []
    for school, seasons in sorted(fixable.items()):
        row = by_school[school]
        host = CONF_HOSTS.get(row["conference"])
        fixed_any = False
        if host:
            slugs = conf_slugs(host, "2025") or conf_slugs(host, "2024")
            slug = best_slug(school, slugs) if slugs else None
            if slug:
                got = []
                for season in seasons:
                    url = season_url("presto", host, slug, season)
                    t = try_url(url)
                    if t is not None and not t.empty:
                        for _, p in t.iterrows():
                            new_stats.append([school, row["conference"], season,
                                              p.get("player", "")] +
                                             [p.get(c, "") for c in STAT_COLS])
                        got.append(season)
                        row[f"url_{season}"] = url
                    time.sleep(0.15)
                if got:
                    fixed_any = True
                    row["platform"] = "presto"
                    print(f"FIXED {school} via {host} slug={slug}: seasons {got}", flush=True)
                    for season in seasons:
                        if season not in got:
                            keep.append({"school": school, "season": season,
                                         "url": season_url("presto", host, slug, season),
                                         "status": "season_missing",
                                         "reason": "conference site lacks this season's page"})
        if not fixed_any:
            print(f"STILL-UNRESOLVED {school} ({row['conference']})", flush=True)
            for season in seasons:
                keep.append({"school": school, "season": season,
                             "url": row.get(f"url_{season}", ""),
                             "status": "unresolved",
                             "reason": "school and conference sites both failed"})

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
    print(f"cleanup done: +{len(new_stats)} rows, {len(keep)} gaps remain")


if __name__ == "__main__":
    main()
