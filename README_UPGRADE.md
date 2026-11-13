# NBA LOCKBOX: Walk-Forward Research Audit (Upgrade 1)

## Why this matters
The current rolling-form and hit-rate files show what happened *recently*, but not what happened in a new game **after** a threshold had built a strong prior record. This adds a historical walk-forward audit and shortfall diagnostics without sportsbook odds, tickets, stakes, or future-data leakage.

## Add to GitHub
Copy the three files in this ZIP into the same relative paths in `deucetg/NBA-lockbox-reseasrch`:

- `nba_walkforward_audit.py` — new root-level Python research exporter
- `tests/test_walkforward_audit.py` — regression tests
- `.github/workflows/nba-walkforward-audit.yml` — new scheduled and manually runnable GitHub Action

Do **not** upload the `research_exports/` test data from a local machine. The workflow reads the current repository `research_exports/player_last20.csv` produced by the existing exporter.

Manual run: GitHub → Actions → **NBA LOCKBOX Walk-Forward Research Audit** → **Run workflow**. Verify the workflow is green before relying on new exports.

## New exports
- `research_exports/walkforward_signal_evaluations.csv` — for each historical player-game and market threshold with a *pre-game* L5 4/5+ or complete L8 7/8+ trend, the future outcome and actual minutes.
- `research_exports/walkforward_signal_summary.csv` — observed next-game hit/miss counts by market, threshold and pregame L5 hit count. This is descriptive validation, not a prediction or expected win probability.
- `research_exports/walkforward_perfect_streak_misses.csv` — cases with pregame L5 5/5 that missed in the next game; highlights cases where actual minutes were under 80% of the prior five-game average.
- `research_exports/walkforward_status.json` — generation date, sample scope and limitations.

## Data safety
- Uses stable player IDs, not names alone; keeps team assignment attached to the game date.
- Uses games on dates **strictly before** the target game's date for every pregame metric; same-date data is excluded to prevent uncertain timing leakage.
- Requires five prior eligible game logs. L8 hit count is blank unless all eight earlier games are available.
- Only the last 20 eligible games *per player* are in the source file, so the backtest is limited in coverage.
- Retains all distinct thresholds as correlated observations. Do not interpret signal count as independent bettors or bets.
- Reduced minutes are an association, **not proven causal explanation**. No injury or lineup facts inferred.
- Does not include official NBA.com tracking, steals/blocks, exact opponent positions, sportsbook prices or probabilities.

## Testing locally
```sh
python -m unittest discover -s tests -p 'test_*.py' -v
python nba_walkforward_audit.py
```

## Next planned upgrade
Integrate official NBA.com potential assists and rebound chances with source/date verification, plus a roster-validity and injury audit before each matchup. Keep manual tracking figures explicitly labeled until verified ingestion exists.
