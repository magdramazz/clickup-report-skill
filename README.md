# clickup-report — a Claude Code skill

Turn your ClickUp tickets into a **meeting-ready Markdown report** in one
sentence:

> make my clickup report for my meeting with my manager

Claude reads every ticket you are assigned in the **latest sprint**: the full
description and the latest comments. It then writes a report organized **by
sprint → by status**, with a short summary of each ticket, the risks, and
talking points you can say out loud.

It works for any developer, whatever language or framework you use: the skill
only talks to ClickUp.

---

## What you get

```
# Sprint report — Jane Doe
Sprint: Sprint 12 (2026-03-02 → 2026-03-15) · Where: Web App / Sprints

## Executive summary          ← 3–5 bullets: outcomes, counts, biggest risk, what's next
## At a glance                ← counts per status + key metrics
   | Sprint    | to do | in progress | in review | done | Total |
   | Sprint 12 |   2   |      3      |     1     |  4   |  10   |
## Blocked / at risk          ← overdue, blocked, stale, QA send-backs, "can't test" comments
## Talking points             ← sentences you can say in the meeting
## Tasks by sprint / list
### Sprint 12 (10)
#### To do (2)
##### [Add CSV export to the orders page](https://app.clickup.com/t/…)
Status: to do · Priority: high · Due: 2026-03-12
Admins need to export filtered orders to CSV for the finance team. Work has not started;
the product owner confirmed the column list in a comment on 03-04.
#### In progress (3)
…
#### In review (1)
…
#### Done (4)
…
## Data notes                 ← anything the data could not show (e.g. no time tracking)
```

Statuses use **your workspace's own names and order** ("ready for qa",
"in review", "blocked" and so on).

## Features

- **First-run setup:** Claude asks for your ClickUp user id, workspace id and
  API token. It checks them against the ClickUp API and saves them. If you
  don't know your user id, it looks it up from the token and asks you to
  confirm.
- **Latest sprint by date:** it finds the sprint with the most recent start
  date that has already started. A sprint created in advance is skipped. You
  can also ask for a named sprint, or a date period ("last 14 days").
- **Real ticket summaries:** each ticket gets 2–4 sentences: what it is about,
  what happened (taken from the comments), and where it stands now. This
  works even when tickets are written in another language or use a
  user-story template.
- **Risk detection:** overdue, blocked or on-hold statuses, no update for 14+
  days, tracked time over the estimate, and blockers in comments ("can't
  test", "waiting on…"). Claude also reads the comments for QA send-backs.
- **Asks instead of guessing:** if an id, a sprint or a period is unclear,
  Claude asks.
- **Read-only:** the skill only sends GET requests, so it never changes
  anything in ClickUp.

## Install

Requirements:
- [Claude Code](https://claude.com/claude-code)
- Python 3.8+ (standard library only; nothing to `pip install`)

```bash
# all your projects (recommended)
git clone https://github.com/magdramazz/clickup-report-skill ~/.claude/skills/clickup-report

# or one project only
git clone https://github.com/magdramazz/clickup-report-skill <project>/.claude/skills/clickup-report
```

On Windows, `~` is `C:\Users\<you>`. Restart Claude Code after you install it.

## Use

Just ask:

| You say | You get |
|---|---|
| `make my clickup report` | the latest sprint, if that is your saved default |
| `report for the last sprint for my 1:1` | the latest sprint, chosen by date |
| `clickup report for Sprint 6` | that sprint (Claude asks if the name matches several lists) |
| `what did I do in the last 14 days in clickup` | a date period instead of a sprint |
| `/clickup-report` | runs the skill directly |

Reports are saved to `~/clickup-reports/clickup-report-<date>.md`.

### First run

Claude asks for:

1. **User id**: you can answer "I don't know"; Claude finds it from the token.
2. **Workspace id**: the first number in any ClickUp URL:
   `app.clickup.com/<workspace id>/...`
3. **API token**: in ClickUp, *Settings → Apps → API Token* (starts with
   `pk_`).

Optional: the report folder, the report language (default English), and the
default scope ("latest sprint" or "ask me each time").

**To keep the token out of the chat,** run the setup yourself in a terminal.
It hides the token while you type:

```bash
python ~/.claude/skills/clickup-report/scripts/setup_config.py
```

### Change settings later

```bash
python scripts/setup_config.py --status                        # show settings (token masked)
python scripts/setup_config.py --default-scope latest-sprint   # or: ask
python scripts/setup_config.py --group-by tag                  # list | folder | space | tag | status
python scripts/setup_config.py --language Arabic
python scripts/setup_config.py --report-dir ~/Documents/reports
python scripts/setup_config.py --reset                         # forget everything
```

Or ask Claude, for example: *"change my clickup token"*.

## Privacy and security

- **Read-only:** the scripts only send GET requests to `api.clickup.com`.
- **Where the token is stored:** in your user config folder, never in a
  project or a report:
  - Windows: `%APPDATA%\clickup-report\config.json`
  - macOS/Linux: `~/.config/clickup-report/config.json` (file mode `600`)
- The token is only ever printed masked (`pk_1…AB12`).
- If `CLICKUP_API_TOKEN` is set in your environment, it is used only when no
  token is saved.
- Temporary task data is written to a temp folder and deleted after the report
  is built.

## How it works

| File | Role |
|---|---|
| `SKILL.md` | The instructions Claude follows (ask → fetch → build → write summaries → check) |
| `scripts/setup_config.py` | First-run setup, validation, id discovery, preferences, reset |
| `scripts/fetch_tasks.py` | Finds the sprint, fetches your tasks and comments, sorts them, flags risks |
| `scripts/build_report.py` | Renders the Markdown and leaves markers where Claude writes the summaries |
| `scripts/clickup_client.py` | Config storage and a small read-only ClickUp API v2 client (retries, rate limits) |
| `references/clickup-api.md` | API notes and troubleshooting |

The scripts do everything that must be exact: the API calls, the counts, the
dates and the tables. Claude does what needs judgment: reading the tickets,
writing the summaries, and spotting risks that keywords miss.

## Troubleshooting

| Problem | Fix |
|---|---|
| `HTTP 401` | The token is wrong or was regenerated. Run the setup again. |
| "Workspace … is not visible to this token" | You gave a space or list id. The workspace id is the **first** number in the URL. |
| "no started sprint found" | No list has a start and due date. Ask for a date period instead, or set dates on your sprint lists. |
| Tracked time shows `—` | Your workspace does not use ClickUp time tracking. |
| A ticket is missing | It is not **assigned** to you (only watching it does not count), or it is in another sprint. |

More: [`references/clickup-api.md`](references/clickup-api.md).

## License

[MIT](LICENSE)
