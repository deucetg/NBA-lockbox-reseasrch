"""NBA LOCKBOX walk-forward research calibration (no sportsbook data).

At each historical game date, assess thresholds using *only earlier game dates*.
Record the next-game outcome separately. A historical signal is not a
probability or a betting recommendation. Runs on player_last20.csv, so the
sample is limited to the latest 20 games per player in the current export.
"""

import argparse
import csv
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

MARKETS = {
    "PTS": ("pts", (10, 15, 20, 25, 30)),
    "REB": ("reb", (4, 5, 6, 8, 10)),
    "AST": ("ast", (2, 4, 5, 6)),
    "3PM": ("three_pm", (1, 2, 3)),
    "PRA": ("PRA", (20, 25, 30, 35)),
    "PR": ("PR", (15, 20, 25)),
    "PA": ("PA", (15, 20, 25)),
    "RA": ("RA", (6, 8, 10)),
}
REQUIRED = {"game_id", "game_date", "player_id", "player_name", "team_id",
            "minutes", "pts", "reb", "ast", "three_pm"}
EVALUATION_COLUMNS = [
    "game_date", "game_id", "player_id", "player_name", "team_id",
    "market", "threshold", "prior_L5_hits", "prior_L5_games",
    "prior_L8_hits", "prior_L8_games", "prior_L5_stat_avg",
    "prior_L5_minutes_avg", "prior_latest_game_date", "team_change_in_L5",
    "actual_stat", "next_game_hit", "actual_minutes", "minutes_ratio",
    "minutes_drop_20pct", "research_signal",
]
SUMMARY_COLUMNS = [
    "market", "threshold", "prior_L5_hits", "signal_count",
    "next_game_hits", "next_game_misses", "observed_next_hit_pct",
    "misses_with_minutes_drop_20pct",
]


def read_logs(path):
    """Deduplicate by player and game, reject malformed rows, never infer teams."""
    with Path(path).open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        missing = REQUIRED - set(reader.fieldnames or [])
        if missing:
            raise ValueError("player_last20.csv is missing: " + ", ".join(sorted(missing)))
        by_player = defaultdict(dict)
        for r in reader:
            try:
                date = str(r["game_date"])
                if len(date) != 8 or not date.isdigit():
                    continue
                dt = datetime.strptime(date, "%Y%m%d")
                mins = float(r["minutes"])
                if mins <= 0:
                    continue
                pts, reb, ast, threes = (int(float(r[k])) for k in
                                         ("pts", "reb", "ast", "three_pm"))
                if min(pts, reb, ast, threes) < 0:
                    continue
                pid, gid = str(r["player_id"]), str(r["game_id"])
                if not pid or not gid:
                    continue
            except (ValueError, TypeError, KeyError):
                continue
            record = {
                "game_id": gid, "game_date": dt.strftime("%Y%m%d"),
                "player_id": pid, "player_name": r["player_name"],
                "team_id": str(r["team_id"]), "minutes": mins,
                "pts": pts, "reb": reb, "ast": ast, "three_pm": threes,
                "PRA": pts + reb + ast, "PR": pts + reb,
                "PA": pts + ast, "RA": reb + ast,
            }
            old = by_player[pid].get(gid)
            if old is not None and old != record:
                raise ValueError(f"Conflicting duplicate for player {pid}, game {gid}")
            by_player[pid][gid] = record
    return {
        pid: sorted(games.values(), key=lambda g: (g["game_date"], g["game_id"]))
        for pid, games in by_player.items()
    }


def evaluate(by_player):
    """Only pregame L5/L8 results are features; today's box score is outcome."""
    evaluations = []
    eligible_player_games = 0
    for pid, games in by_player.items():
        for game in games:
            # Excluding the *entire same date* avoids intra-day order leakage.
            prior = [g for g in games if g["game_date"] < game["game_date"]]
            if len(prior) < 5:
                continue
            eligible_player_games += 1
            prior = prior[-8:]
            l5 = prior[-5:]
            l8_complete = len(prior) == 8
            prior_minutes = sum(g["minutes"] for g in l5) / 5
            minutes_ratio = game["minutes"] / prior_minutes if prior_minutes > 0 else None
            minutes_drop = minutes_ratio is not None and minutes_ratio < 0.8
            team_change = any(g["team_id"] != game["team_id"] for g in l5)
            for market, (field, thresholds) in MARKETS.items():
                for threshold in thresholds:
                    l5hits = sum(g[field] >= threshold for g in l5)
                    l8hits = sum(g[field] >= threshold for g in prior) if l8_complete else ""
                    # Retain high historical consistency signals, not every
                    # trivially inapplicable threshold for every player.
                    if l5hits < 4 and not (l8_complete and l8hits >= 7):
                        continue
                    hit = int(game[field] >= threshold)
                    signal = "L5_5of5" if l5hits == 5 else "L5_4of5_or_L8_7of8"
                    evaluations.append({
                        "game_date": game["game_date"], "game_id": game["game_id"],
                        "player_id": pid, "player_name": game["player_name"],
                        "team_id": game["team_id"], "market": market,
                        "threshold": threshold, "prior_L5_hits": l5hits,
                        "prior_L5_games": 5, "prior_L8_hits": l8hits,
                        "prior_L8_games": 8 if l8_complete else len(prior),
                        "prior_L5_stat_avg": round(sum(g[field] for g in l5) / 5, 2),
                        "prior_L5_minutes_avg": round(prior_minutes, 2),
                        "prior_latest_game_date": prior[-1]["game_date"],
                        "team_change_in_L5": "yes" if team_change else "no",
                        "actual_stat": game[field], "next_game_hit": hit,
                        "actual_minutes": round(game["minutes"], 2),
                        "minutes_ratio": round(minutes_ratio, 3) if minutes_ratio is not None else "",
                        "minutes_drop_20pct": "yes" if minutes_drop else "no",
                        "research_signal": signal,
                    })
    evaluations.sort(key=lambda r: (r["game_date"], r["game_id"],
                                    r["player_id"], r["market"], r["threshold"]))
    return evaluations, eligible_player_games


def summaries(evaluations):
    grouped = defaultdict(list)
    for e in evaluations:
        grouped[(e["market"], e["threshold"], e["prior_L5_hits"])].append(e)
    result = []
    for (market, threshold, hits), records in sorted(grouped.items()):
        n = len(records)
        wins = sum(r["next_game_hit"] for r in records)
        result.append({
            "market": market, "threshold": threshold, "prior_L5_hits": hits,
            "signal_count": n, "next_game_hits": wins,
            "next_game_misses": n - wins,
            "observed_next_hit_pct": round(100 * wins / n, 1),
            "misses_with_minutes_drop_20pct": sum(
                r["next_game_hit"] == 0 and r["minutes_drop_20pct"] == "yes"
                for r in records),
        })
    return result


def write_csv(path, rows, columns):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def run(source, output):
    players = read_logs(source)
    evaluations, player_games = evaluate(players)
    if not evaluations:
        raise ValueError("No evaluable pregame signals; no files published")
    output = Path(output)
    summaries_data = summaries(evaluations)
    perfect_misses = [r for r in evaluations if r["prior_L5_hits"] == 5
                      and not r["next_game_hit"]]
    write_csv(output / "walkforward_signal_evaluations.csv",
              evaluations, EVALUATION_COLUMNS)
    write_csv(output / "walkforward_signal_summary.csv",
              summaries_data, SUMMARY_COLUMNS)
    write_csv(output / "walkforward_perfect_streak_misses.csv",
              perfect_misses, EVALUATION_COLUMNS)
    status = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "latest_evaluated_date": max(e["game_date"] for e in evaluations),
        "earliest_evaluated_date": min(e["game_date"] for e in evaluations),
        "input": str(source), "eligible_player_games": player_games,
        "signal_evaluations": len(evaluations),
        "summary_groups": len(summaries_data),
        "perfect_L5_misses": len(perfect_misses),
        "perfect_L5_misses_with_20pct_minutes_drop": sum(
            e["minutes_drop_20pct"] == "yes" for e in perfect_misses),
        "markets": list(MARKETS),
        "notes": [
            "Research only: no odds, stakes, tickets or model probabilities.",
            "Walk-forward: games on and after the target date are never used as predictors.",
            "Only L5 4/5+ or complete L8 7/8+ signals evaluated; observational selection bias.",
            "player_last20.csv retains up to 20 games/player; older history is unavailable.",
            "Minutes below 80% of prior L5 marked as association, not causal diagnosis.",
            "No lineup, injury, opponent-by-position, potential assists or rebound chances.",
            "Results for different thresholds and same-game players are correlated.",
        ],
    }
    (output / "walkforward_status.json").write_text(
        json.dumps(status, indent=2) + "\n", encoding="utf-8")
    return status


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", default="research_exports/player_last20.csv")
    parser.add_argument("--output", default="research_exports")
    args = parser.parse_args()
    print(json.dumps(run(Path(args.input), Path(args.output)), indent=2))


if __name__ == "__main__":
    main()
