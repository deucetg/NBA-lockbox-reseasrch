NBA LOCKBOX — Chat Handoff

Last documented: 2026-11-13
Repository: https://github.com/deucetg/NBA-lockbox-reseasrch

Mission

Build an automated, verifiable NBA research platform. Keep research data separate from betting tickets. Never invent player stats, coverage, or workflow outcomes.

Working preferences

One step at a time. Provide direct GitHub links, complete downloadable files when needed, and minimal manual copy/paste. Inspect repository state before proposing edits; preserve working features.

Current priority: Shooting Momentum Scanner

Planned profiles:

• Early Hot Hand: >=3 three-point attempts per game and >=12 minutes per game, L3.
• Emerging Volume: >=4 3PA/G and >=15 MPG, L5.
• Established Shooter: >=6 3PA/G and >=20 MPG, L5.
• Early Scoring Breakout: >=5 field-goal attempts per game and >=12 MPG.
• Emerging Scorer: >=8 FGA/G and >=15 MPG.
• Established Scorer: >=12 FGA/G and >=20 MPG.
• Additional low-volume breakout watchlists for both three-point attempts and total field-goal attempts.
• Proposed alerts: >=25% recent attempt-volume acceleration, >=8 percentage-point shooting-efficiency improvement. Compare against the PRECEDING non-overlapping five games, not an overlapping L5 period.
• Identify newly hot players and bench/role players; do not assume minutes alone proves bench status.
• Use aggregate makes/attempts to compute percentages. Require adequate samples. Walk-forward backtest signals before assigning predictive grades.

Repository verification on 2026-11-13

• research_exports/player_last20.csv was successfully fetched; header includes game_id, player_id, player_name, team_id, game_date, minutes, pts, reb, ast, three_pm, fgm, fga, three_pa, etc. This is a potential input to the scanner, but complete coverage and freshness still require verification.
• docs/CHAT_HANDOFF.md, docs/PROJECT_PROGRESS.md, and nba_shooting_momentum.py returned 404 on the default branch at the time of the check. Recheck before proceeding; user may have uploaded since.
• Earlier project work reportedly established nba_walkforward_audit.py and related workflow/exports. Reverify in repository rather than assuming current state.

NBA.com tracking limitation

A GitHub Actions NBA.com tracking connection test for passing timed out. Do not repeatedly propose the same direct NBA.com GitHub-hosted request without new evidence. User accepts manually supplying NBA.com last-five passing, rebound-chance, and shooting tracking tables. Preserve source, date, and sample window; do not treat L5 aggregate snapshots as individual game logs or substitute assists for potential assists.

Immediate next actions

1. Upload this handoff document at docs/CHAT_HANDOFF.md.
2. Create and maintain docs/PROJECT_PROGRESS.md.
3. Verify whether the updated nba_shooting_momentum.py is on GitHub; inspect source and test it against actual research exports before adding a workflow.
4. Build backtests and only then evaluate signal quality.

New chat starter

Inspect this file and docs/PROJECT_PROGRESS.md in the linked repository. Confirm current files and workflows. Summarize what is verified, what failed, and the ONE next step. Do not rely on chat history alone.
