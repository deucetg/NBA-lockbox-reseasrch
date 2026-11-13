"""Offline unit tests for NBA LOCKBOX walk-forward threshold evaluations."""
import csv
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from nba_walkforward_audit import read_logs, evaluate, run


class WalkforwardTests(unittest.TestCase):
    def make_csv(self, path, add_same_date=False, later=True):
        fields = ("game_id", "game_date", "player_id", "player_name", "team_id",
                  "minutes", "pts", "reb", "ast", "three_pm")
        rows = []
        for i, day in enumerate(range(1, 7), start=1):
            rows.append(dict(game_id=str(i), game_date=f"202611{day:02}",
                             player_id="10", player_name="Test Player",
                             team_id="16", minutes="30", pts="20", reb="8",
                             ast="5", three_pm="2"))
        rows.append(dict(game_id="7", game_date="20261107", player_id="10",
                         player_name="Test Player", team_id="16",
                         minutes="18", pts="9", reb="3", ast="1", three_pm="0"))
        if add_same_date:
            rows.append(dict(game_id="8", game_date="20261107", player_id="10",
                             player_name="Test Player", team_id="16",
                             minutes="42", pts="40", reb="25", ast="10", three_pm="5"))
        if later:
            rows.append(dict(game_id="9", game_date="20261108", player_id="10",
                             player_name="Test Player", team_id="16",
                             minutes="31", pts="20", reb="8", ast="5", three_pm="2"))
        with path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fields)
            writer.writeheader()
            writer.writerows(reversed(rows))  # deliberately reverse input chronology

    def test_perfect_rebound_signal_misses_when_minutes_drop(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "logs.csv"
            self.make_csv(p, later=False)
            signals, games = evaluate(read_logs(p))
            reb = [x for x in signals if x["game_id"] == "7"
                   and x["market"] == "REB" and x["threshold"] == 8]
            self.assertEqual(games, 2)
            self.assertEqual(len(reb), 1)
            self.assertEqual(reb[0]["prior_L5_hits"], 5)
            self.assertEqual(reb[0]["prior_L8_hits"], "")
            self.assertEqual(reb[0]["prior_L8_games"], 6)
            self.assertEqual(reb[0]["actual_stat"], 3)
            self.assertEqual(reb[0]["next_game_hit"], 0)
            self.assertEqual(reb[0]["minutes_drop_20pct"], "yes")

    def test_never_leaks_same_day_or_future_games(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "logs.csv"
            self.make_csv(p, add_same_date=True, later=True)
            signals, _ = evaluate(read_logs(p))
            todays = [x for x in signals if x["game_id"] == "7"
                      and x["market"] == "REB" and x["threshold"] == 8]
            self.assertEqual(len(todays), 1)
            self.assertEqual(todays[0]["prior_L5_stat_avg"], 8.0)
            self.assertEqual(todays[0]["prior_latest_game_date"], "20261106")
            self.assertEqual(todays[0]["prior_L5_hits"], 5)

    def test_exports_research_only_and_captures_perfect_miss(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "logs.csv"
            o = Path(d) / "out"
            self.make_csv(p, later=False)
            status = run(p, o)
            self.assertGreater(status["perfect_L5_misses"], 0)
            for filename in ("walkforward_signal_evaluations.csv",
                             "walkforward_signal_summary.csv",
                             "walkforward_perfect_streak_misses.csv",
                             "walkforward_status.json"):
                self.assertTrue((o / filename).exists(), filename)
            contents = " ".join((o / n).read_text() for n in
                                ("walkforward_signal_evaluations.csv",
                                 "walkforward_signal_summary.csv"))
            self.assertNotIn("sportsbook", contents.lower())
            self.assertNotIn("wager", contents.lower())


if __name__ == "__main__":
    unittest.main()
