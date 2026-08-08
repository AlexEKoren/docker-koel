#!/usr/bin/env python3
"""Pull per-player batting stats for every school/season in d3_baseball_stats_urls.csv.

Outputs:
  d3_batting_stats.csv - one row per player-season: school, conference, season,
                         player, avg, gp, ab, r, h, 2b, 3b, hr, rbi, bb, so, sb, obp, slg
  d3_batting_gaps.csv  - one row per school-season that could not be fetched or
                         parsed: school, season, url, status, reason

Requires: requests, pandas, lxml  (pip install requests pandas lxml)

Run from a machine/environment with open outbound HTTPS to school athletics
sites; this does not work behind an egress-restricted proxy.
"""
import csv
import re
import sys
import time

import pandas as pd
import requests

SEASONS = ["2026", "2025", "2024", "2023", "2022"]
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/126.0 Safari/537.36"}
STAT_COLS = ["avg", "gp", "ab", "r", "h", "2b", "3b", "hr", "rbi", "bb", "so", "sb", "obp", "slg"]

# Column aliases seen across Sidearm/Presto batting tables
ALIASES = {
    "avg": {"avg", "ba", "batting avg"},
    "gp": {"gp", "g", "gp-gs", "games"},
    "ab": {"ab", "at bats"},
    "r": {"r", "runs"},
    "h": {"h", "hits"},
    "2b": {"2b"},
    "3b": {"3b"},
    "hr": {"hr"},
    "rbi": {"rbi", "rbis"},
    "bb": {"bb"},
    "so": {"so", "k"},
    "sb": {"sb", "sb-att"},
    "obp": {"obp", "ob%", "obpct"},
    "slg": {"slg", "slg%", "slgpct"},
}
PLAYER_ALIASES = {"player", "name", "batter"}


def normalize_cols(df):
    cols = {}
    for c in df.columns:
        key = re.sub(r"\s+", " ", str(c)).strip().lower()
        if key in PLAYER_ALIASES:
            cols[c] = "player"
            continue
        for canon, names in ALIASES.items():
            if key in names:
                cols[c] = canon
                break
    return df.rename(columns=cols)


def pick_batting_table(tables):
    """Choose the table that looks like individual batting stats."""
    best, best_score = None, 0
    for t in tables:
        t = normalize_cols(t)
        have = set(t.columns)
        score = len(have & {"player", "avg", "ab", "h", "r"})
        if "player" in have and "ab" in have and score > best_score:
            best, best_score = t, score
    return best


def clean(df):
    df = df[[c for c in ["player"] + STAT_COLS if c in df.columns]].copy()
    df["player"] = df["player"].astype(str).str.strip()
    # Drop totals/opponent rows and blanks
    df = df[~df["player"].str.lower().str.match(r"(totals?|opponents?|team|nan)\b.*", na=True)]
    df = df[df["player"] != ""]
    return df


def fetch_season(url):
    """Return (dataframe, status, reason). dataframe is None on failure."""
    try:
        resp = requests.get(url, headers=UA, timeout=30)
    except requests.RequestException as e:
        return None, "fetch_error", type(e).__name__
    if resp.status_code != 200:
        return None, f"http_{resp.status_code}", "page not found or blocked"
    try:
        tables = pd.read_html(resp.text)
    except ValueError:
        return None, "no_tables", "page has no parseable stats tables"
    table = pick_batting_table(tables)
    if table is None:
        return None, "no_batting_table", "no table with player/AB columns found"
    table = clean(table)
    if table.empty:
        return None, "empty_table", "batting table parsed but had no player rows"
    return table, "ok", ""


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else "d3_baseball_stats_urls.csv"
    stats_rows, gap_rows = [], []
    with open(src, newline="") as f:
        schools = list(csv.DictReader(f))
    for i, row in enumerate(schools, 1):
        school, conf = row["school"], row["conference"]
        print(f"[{i}/{len(schools)}] {school}", flush=True)
        for season in SEASONS:
            url = row.get(f"url_{season}", "").strip()
            if not url:
                gap_rows.append([school, season, "", "no_url", "no URL (program did not exist that season or URL unknown)"])
                continue
            table, status, reason = fetch_season(url)
            if table is None:
                gap_rows.append([school, season, url, status, reason])
            else:
                for _, p in table.iterrows():
                    stats_rows.append([school, conf, season, p.get("player", "")] +
                                      [p.get(c, "") for c in STAT_COLS])
            time.sleep(0.5)  # be polite to school servers

    with open("d3_batting_stats.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["school", "conference", "season", "player"] + STAT_COLS)
        w.writerows(stats_rows)
    with open("d3_batting_gaps.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["school", "season", "url", "status", "reason"])
        w.writerows(gap_rows)
    print(f"done: {len(stats_rows)} player-seasons, {len(gap_rows)} gaps")


if __name__ == "__main__":
    main()
