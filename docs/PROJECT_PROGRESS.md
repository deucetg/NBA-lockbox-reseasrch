# NBA LOCKBOX — Project Progress

Last reviewed: 2026\-11\-13
Repository: https://github\.com/deucetg/NBA\-lockbox\-reseasrch

## Purpose

A durable, evidence\-based development log for continuing work across ChatGPT conversations\. Keep research\-only data separate from betting tickets\. Verify GitHub state before treating anything as complete\.

## Verified in GitHub

- `docs/CHAT_HANDOFF.md` exists on `main` &#40;verified 2026\-11\-13&#41;\.
- `research_exports/player_last20.csv` exists and contains player\-game shooting fields including `three_pm`, `three_pa`, `fgm`, `fga`, `minutes`, player IDs, game IDs and dates &#40;verified 2026\-11\-13&#41;\. Coverage, completeness and freshness have not been audited for the new scanner\.

## Not yet verified / incomplete

- `docs/PROJECT_PROGRESS.md`: this file must be uploaded and verified\.
- `nba_shooting_momentum.py`: not found on `main` during the 2026\-11\-13 check\. User may have a downloaded copy; check again before replacing anything\.
- Shooting Momentum Scanner has not been validated against the full research export or scheduled as a verified successful GitHub workflow\.
- Walk\-forward audit scripts and outputs were discussed in earlier development; independently verify their current GitHub state before using their results\.

## Current feature specification: Shooting Momentum Scanner

### Three\-point profiles

- Early Hot Hand: L3, \>=3 3PA/game, \>=12 minutes/game\.
- Emerging Volume: L5, \>=4 3PA/game, \>=15 minutes/game\.
- Established Shooter: L5, \>=6 3PA/game, \>=20 minutes/game\.

### Overall scoring profiles &#40;proposed&#41;

- Early Scoring Breakout: \>=5 FGA/game, \>=12 minutes/game\.
- Emerging Scorer: \>=8 FGA/game, \>=15 minutes/game\.
- Established Scorer: \>=12 FGA/game, \>=20 minutes/game\.
- Include separate low\-volume three\-point and overall shot\-volume breakout watchlists\.

### Signal rules

- Proposed \>=25% recent shot\-attempt acceleration and \>=8 percentage\-point efficiency improvement\.
- Compare recent L3/L5 windows with the preceding **non\-overlapping five games**\.
- Use total makes / total attempts, not the arithmetic mean of per\-game shooting percentages\.
- Detect newly qualifying players by comparing successive historical scan dates\.
- Preserve sample sizes and exclude insufficient history from claims of acceleration\.
- Do not infer confirmed bench status solely from minutes; mark it as a proxy until reliable starter status is available\.
- Walk\-forward backtest on games strictly after each signal; no predictive grades until tested\.

## Data limitations and known failures

- Direct NBA\.com tracking requests from GitHub Actions timed out in a passing endpoint test\. Do not repeat without a genuinely different approach and evidence\.
- User is willing to supply NBA\.com last\-five passing, rebound\-chance and shooting tables manually\. Preserve source date, time window and aggregation level\. Do not fabricate potential assists or rebound chances\.

## Next single step after this file is verified

1. Re\-check whether `nba_shooting_momentum.py` is on `main`\.
2. If present, inspect its implementation and run tests against `research_exports/player_last20.csv`; otherwise prepare a verified complete script for upload\.
3. Only after passing tests, connect the scanner to an Actions workflow and check generated outputs\.

## Update protocol

After each milestone, add a dated entry with: change, exact paths, verification evidence &#40;test/workflow result or GitHub link&#41;, known limitations and next step\. Never mark a planned feature complete just because a file was drafted\.

## Milestones

- 2026\-11\-13: `docs/CHAT_HANDOFF.md` verified on GitHub `main`\.
- 2026\-11\-13: Project progress log prepared for upload; awaiting GitHub verification\.
