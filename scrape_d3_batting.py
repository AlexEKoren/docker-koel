#!/usr/bin/env python3
"""Pull per-player batting stats for every school/season in d3_baseball_stats_urls.csv.

Outputs:
  d3_batting_stats.csv - one row per player-season: school, conference, season,
                         player, avg, gp, ab, r, h, 2b, 3b, hr, rbi, bb, so, sb, obp, slg
  d3_batting_gaps.csv  - one row per school-season that could not be fetched or
                         parsed: school, season, url, status, reason

Requires: requests, pandas, lxml  (pip install requests pandas lxml)
"""
import csv
import io
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import requests

SEASONS = ["2026", "2025", "2024", "2023", "2022"]
UA = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.google.com/",
}
STAT_COLS = ["avg", "gp", "ab", "r", "h", "2b", "3b", "hr", "rbi", "bb", "so", "sb", "obp", "slg"]
WORKERS = 12

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
TOTALS = re.compile(r"^(totals?|opponents?|team|tm)\b", re.I)


def normalize_cols(df):
    cols = {}
    for c in df.columns:
        key = re.sub(r"\s+", " ", str(c)).strip().lower()
        if key in PLAYER_ALIASES:
            cols[c] = "player"
            continue
        if key.startswith("unnamed") and df[c].dtype == object:
            cols[c] = "player"
            continue
        for canon, names in ALIASES.items():
            if key in names:
                cols[c] = canon
                break
    df = df.rename(columns=cols)
    return df.loc[:, ~df.columns.duplicated()]


def clean_players(df):
    df = df.copy()
    df["player"] = df["player"].astype(str).str.strip()
    # Sidearm renders "Name, First  12 Name, First" (link + number + span) - collapse it
    df["player"] = df["player"].str.replace(r"^(.+?)\s+\d+\s+\1$", r"\1", regex=True)
    df = df[~df["player"].str.lower().isin({"", "nan"})]
    df = df[~df["player"].str.match(TOTALS, na=True)]
    return df


def extract(html_text):
    """Return a per-player batting DataFrame from a stats page, or None."""
    try:
        tables = [normalize_cols(t) for t in pd.read_html(io.StringIO(html_text))]
    except ValueError:
        return None
    # Primary batting table: has player + ab (+ ideally avg/h)
    batting, batting_score = None, 0
    for t in tables:
        have = set(t.columns)
        if "player" in have and "ab" in have:
            score = len(have & {"avg", "h", "rbi", "hr", "bb"})
            if score > batting_score or batting is None:
                batting, batting_score = t, score
    if batting is None:
        return None
    batting = clean_players(batting)
    if batting.empty:
        return None
    # Presto splits runs/steals into a second table keyed by the same player column
    if "r" not in batting.columns:
        for t in tables:
            have = set(t.columns)
            if "player" in have and "r" in have and "ab" not in have and "ip" not in str(have):
                if "era" in have or "app" in have:
                    continue  # pitching table
                supp = clean_players(t)[["player"] + [c for c in ["r", "sb"] if c in t.columns]]
                batting = batting.merge(supp, on="player", how="left", suffixes=("", "_y"))
                break
    keep = ["player"] + [c for c in STAT_COLS if c in batting.columns]
    return batting[keep]


def fetch_season(url):
    try:
        resp = requests.get(url, headers=UA, timeout=30)
    except requests.RequestException as e:
        return None, "fetch_error", type(e).__name__
    if resp.status_code != 200:
        return None, f"http_{resp.status_code}", "page not found or blocked"
    table = extract(resp.text)
    if table is None:
        return None, "no_batting_table", "no table with player/AB columns found"
    if table.empty:
        return None, "empty_table", "batting table parsed but had no player rows"
    return table, "ok", ""


def do_school(row):
    stats, gaps = [], []
    school, conf = row["school"], row["conference"]
    for season in SEASONS:
        url = row.get(f"url_{season}", "").strip()
        if not url:
            gaps.append([school, season, "", "no_url",
                        "no URL (program did not exist that season or URL unknown)"])
            continue
        table, status, reason = fetch_season(url)
        if table is None:
            gaps.append([school, season, url, status, reason])
        else:
            for _, p in table.iterrows():
                stats.append([school, conf, season, p.get("player", "")] +
                             [p.get(c, "") for c in STAT_COLS])
        time.sleep(0.2)
    return school, stats, gaps


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else "d3_baseball_stats_urls.csv"
    with open(src, newline="") as f:
        schools = list(csv.DictReader(f))
    stats_rows, gap_rows = [], []
    done = 0
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for school, stats, gaps in ex.map(do_school, schools):
            done += 1
            stats_rows.extend(stats)
            gap_rows.extend(gaps)
            print(f"[{done}/{len(schools)}] {school}: +{len(stats)} players, {len(gaps)} gaps", flush=True)

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
