# Application reconciliation — 2026-10-17

## Authority and scope

`data/applications.csv` remains the local runtime application ledger. Google Sheets is the actively maintained working tracker and is evidence for the newer milestones imported here. Authority is field-specific: verified dated milestones supersede older statuses; missing or generic Sheet fields never erase precise CSV roles, URLs, dates, or notes. Neither file's modification time alone establishes status authority. No automatic Sheets dependency or bidirectional sync is introduced.

Compared current main `66637d32b806eb9a74f303f54cd634ea6575168c`, the available previous-task checkout, and live `Tracker!A1:M100` (92 populated rows). Contrary to the task's initial count, the CSV contains **43 application records**, plus its header. The accessible checkout has no application-data changes relative to main. A search of accessible workspace/home/temp files found no newer applications.csv. No export/SQLite application ledger is present in the inspected personal_ops checkouts. This does not verify Jacob's separate computer, VPS, or unshared files. Never replace those files with this snapshot without comparing them.

## Reconciliation rules

- Import applied/process/rejected records only; exclude sourcing notes and unapplied leads. Historic pending records stay pending, not inferred rejected due to age.
- Preserve every original field in retained records except verified status changes and one missing duplicate application date. Full imported Sheet row contents, original status, materials, deadlines, and provenance are appended to Notes.
- Unknown application dates stay empty. Date found, acknowledgment date, and last action date are retained in Notes with their original labels; they are not substituted for submission dates.
- BFTF = interview, culture-fit completed October 12, awaiting response; CHEQ = applied, receipt October 7; MONTICELLOAM = rejected October 12; NewCore = interview, scheduled October 18, 11:00–11:30 Asia/Jerusalem. Scheduling history remains in Notes. No unverified outcome is asserted.
- Match Armis/Armis Security by role and June 3 application; Cato by role/date; Runway/GitLab/Chalk/Swap by company and matching date, retaining CSV's precise titles. PlanetScale's aggregate May 8/June 1 Sheet row is appended to its existing June 1 record without inventing a second unknown-role application. Its May 8 role remains unresolved in Notes.
- Collapse two pairs of pre-existing exact nonempty Nebius URL duplicates, preserving alternate titles, notes, dates and sources. The SRE rejection applies to the matching SRE listing; data-center roles remain separate.
- Alice Malware Research Platform, Yotpo Full Stack, Dream Research Infra, and Mobileye's July reapplication stay distinct from older roles/applications. Hostaway's Sheet headline says Accounting but its evidence names AI Platform: use the evidence title and retain the conflicting Sheet title in Notes.

## personal_ops integration audit

Current personal_ops main tree `ace5be8c49cbd419f4a59c5eed319af20800d8dd` has no `ops/jobs.py`. Accessible source/config contains no applications.csv consumer. `VPS_MIGRATION.md` documents jobs retired June 3, bot git-push coupling removed, no job funnel in digests/advisory. The email integration document is a plan, not installed code. Thus the bridge described in job_tracker/CLAUDE.md is stale documentation, not a verified active consumer. A separately deployed older checkout could still read stale local data; deployed state was not accessible and has not been changed. Do not reinstate the retired integration as part of reconciliation.

Sources: https://github.com/DewofyourYouth/personal_ops/blob/main/VPS_MIGRATION.md and the live Google Sheet linked in each imported record.

## Applying to a separate local checkout

Before merging, inspect `git status --short`, uncommitted CSV changes, ignored exports/backups and any local application DB/daily markdown. Commit or back up that work first. Merge this branch normally and resolve CSV conflicts field by field; never use reset --hard, forced checkout, or replace the whole local file. Newer locally verified milestones win, with superseded notes retained. Re-run the CSV integrity checks after merging. This branch does not claim to update a separate running tracker or deployed personal_ops instance.

## Reviewed row decisions

| Sheet row | Company | Action | Local status |
| --- | --- | --- | --- |
| 2 | Fireblocks | add | rejected |
| 4 | Reef Technologies | add | applied |
| 5 | Hostaway | add | rejected |
| 9 | Oasis Security | add | rejected |
| 10 | Armis Security | update | applied → applied |
| 11 | HUMAN Security | add | rejected |
| 12 | Cato Networks | update | applied → rejected |
| 13 | Recorded Future | add | rejected |
| 14 | Runway | update | applied → rejected |
| 15 | Nebius | update | applied → rejected |
| 16 | Lightricks | add | rejected |
| 17 | GitLab | update | applied → applied |
| 18 | april | add | rejected |
| 19 | LiveKit | add | rejected |
| 20 | n8n | add | rejected |
| 21 | Freightos | add | phone_screen |
| 22 | Dream | add | rejected |
| 23 | BlinkOps | add | rejected |
| 24 | Mobileye | add | rejected |
| 25 | Island | update | applied → rejected |
| 26 | Chalk | update | applied → applied |
| 27 | Swap | update | applied → applied |
| 28 | PlanetScale | update | applied → applied |
| 29 | Orca-AI | add | applied |
| 30 | Snyk | update | applied → applied |
| 31 | Together AI | add | applied |
| 32 | ElevenLabs | add | applied |
| 33 | Vercel | add | applied |
| 34 | Port | add | applied |
| 35 | LangChain | add | applied |
| 36 | Balance | add | rejected |
| 37 | Anthropic | add | applied |
| 38 | Deepgram | add | applied |
| 39 | Connecteam | add | interview |
| 45 | DoiT / PerfectScale | add | rejected |
| 46 | Torq | add | rejected |
| 48 | Tenable | add | rejected |
| 58 | HumanSignal | add | applied |
| 59 | Northflank | add | applied |
| 62 | Alice | add | rejected |
| 65 | Buildots | add | rejected |
| 66 | anecdotes | add | rejected |
| 67 | Spacelift | add | rejected |
| 68 | Enveritas | add | rejected |
| 72 | Leidos | add | applied |
| 73 | Mobileye | add | applied |
| 74 | Superlinked | add | applied |
| 75 | Yotpo | add | rejected |
| 76 | Reindeer AI / Team8 | add | applied |
| 77 | Dream | add | rejected |
| 78 | Melio | add | rejected |
| 79 | Viz.ai | add | rejected |
| 80 | Pi Security | add | applied |
| 82 | Fullpath | add | applied |
| 83 | PointFive | add | applied |
| 84 | Glow | add | applied |
| 86 | High Lander | add | applied |
| 87 | Ovalix | add | applied |
| 88 | MIND | add | applied |
| 89 | BFTF | add | interview |
| 90 | CHEQ | add | applied |
| 91 | MONTICELLOAM | add | rejected |
| 92 | NewCore | add | interview |

Excluded Sheet rows (remain available in Sheets): 3, 6, 7, 8, 40, 41, 42, 43, 44, 47, 49, 50, 51, 52, 53, 54, 55, 56, 57, 60, 61, 63, 64, 69, 70, 71, 81, 85.

Result: 43 original records → 94 records; 53 additions; 10 matched-row updates; two existing duplicate rows consolidated.
