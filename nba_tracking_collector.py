
import csv
from pathlib import Path

import requests

BASE = "https://stats.nba.com/stats/leaguedashptstats"
SEASON = "2026-27"
OUT = Path("exports/tracking")

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://www.nba.com/",
    "Origin": "https://www.nba.com",
    "Accept": "application/json",
}


def fetch_tracking(measure):
    params = {
        "College": "",
        "Conference": "",
        "Country": "",
        "DateFrom": "",
        "DateTo": "",
        "Division": "",
        "DraftPick": "",
        "DraftYear": "",
        "GameScope": "",
        "GameSegment": "",
        "Height": "",
        "LastNGames": 0,
        "LeagueID": "00",
        "Location": "",
        "Month": 0,
        "OpponentTeamID": 0,
        "Outcome": "",
        "PORound": 0,
        "PaceAdjust": "N",
        "PerMode": "Totals",
        "Period": 0,
        "PlayerExperience": "",
        "PlayerOrTeam": "Player",
        "PtMeasureType": measure,
        "Rank": "N",
        "Season": SEASON,
        "SeasonSegment": "",
        "SeasonType": "Regular Season",
        "ShotClockRange": "",
        "StarterBench": "",
        "TeamID": 0,
        "VsConference": "",
        "VsDivision": "",
        "Weight": "",
    }

    response = requests.get(
        BASE,
        params=params,
        headers=HEADERS,
        timeout=20,
    )
    response.raise_for_status()

    payload = response.json()
    result = payload["resultSets"][0]

    columns = result["headers"]
    rows = result["rowSet"]

    if not rows:
        raise RuntimeError(
            f"No tracking data returned for {measure}"
        )

    OUT.mkdir(parents=True, exist_ok=True)

    filename = OUT / f"{measure.lower()}_season.csv"

    with filename.open(
        "w", newline="", encoding="utf-8"
    ) as file:
        writer = csv.writer(file)
        writer.writerow(columns)
        writer.writerows(rows)

    print(f"{measure}: {len(rows)} player rows")
    print(f"Saved: {filename}")
    print(f"Available columns: {columns}")


def main():
    for measure in ("Passing", "Rebounding"):
        fetch_tracking(measure)


if __name__ == "__main__":
    main()
