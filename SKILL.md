---
name: clickup-report
description: Turns the user's own ClickUp tickets into a meeting-ready Markdown report. By default it covers the latest sprint, chosen by the sprint's dates, and it can also cover a named sprint or a date period. The report is organized by sprint, then by status (to do, in progress, in review/QA, done), with a short summary of every ticket written after reading its description and comments. It also has blockers and risks, counts, and talking points for the meeting. The first time it runs, it asks for the ClickUp user id, workspace id and API token, checks them, and saves them. Use this skill whenever someone wants a summary, recap, status update or report of their ClickUp tasks or tickets: for a meeting with a boss or manager, a 1:1, a standup, a sprint review or a weekly update. Also use it for requests like "what did I do this sprint", "summarize my tickets" or "prepare my task report", even when the word "report" is not used. It works for any developer, whatever their language or framework.
---

# ClickUp report

Build a meeting-ready Markdown report of the user's ClickUp tasks. The bundled
scripts do the API work and the tables, so the numbers are exact and every run
looks the same. You do three things: ask about anything that is unclear, then
write the short narrative, then check the result.

**Ask; do not guess.** The user said this directly: if something is unclear,
ask. Never invent an id, a period, a status or a number. A wrong report in
front of a boss is worse than a question now.

All scripts are in `scripts/` inside this skill's folder. Run them with
`python` (or `python3` if `python` is missing). They use only the standard
library, so they need no install. Each script prints one JSON object. Read the
`ok`, `error` and `problems` fields before you continue.

## Step 1 — Is the skill set up?

```bash
python scripts/setup_config.py --status
```

Exit code 0 means the skill is configured. Go to Step 3. Exit code 2 means this
is the first run. Go to Step 2.

## Step 2 — First run: ask, check, save

Ask for all three values in **one** message. Also tell the user where to find
each one:

- **ClickUp user id**: a number. ClickUp does not show it clearly in the UI, so
  tell the user they can answer "I don't know". You will then look it up from
  the token (see below) and ask them to confirm it.
- **Workspace (team) id**: the first number in any ClickUp URL:
  `app.clickup.com/<workspace id>/...`
- **API token**: in ClickUp, go to *Settings → Apps → API Token*. It starts
  with `pk_`.

In the same message, offer two optional preferences with their defaults:
- the report folder (default `~/clickup-reports`)
- the report language (default English)

Tell the user that a token pasted into chat stays in this conversation. Offer
the private option: they can run `python "<skill folder>/scripts/setup_config.py"`
in their own terminal, which asks for the token with hidden input. Then they
come back and say "done".

When the values arrive, save them. Pass the token through stdin so that it
does not appear as a command-line argument:

```bash
printf '%s' '<token>' | python scripts/setup_config.py --user-id <id> --team-id <id> --token-stdin [--report-dir <dir>] [--language <lang>]
```

The script checks the values against ClickUp before it saves them:
- **`problems` is not empty** (unknown workspace, user not in the workspace,
  user id is not the token owner): show the problems to the user and ask what
  is right. Re-run with `--force` only if the user confirms the values anyway.
- **HTTP 401**: the token is wrong or revoked. Ask for a new one.
- **The user does not know an id**: run
  `printf '%s' '<token>' | python scripts/setup_config.py --discover --token-stdin`.
  Show the token owner and the workspaces it can see, and let the user pick.
  Do not pick for them, even when there is only one option. A one-line
  confirmation is cheap.

Never repeat the token back in chat, never write it into a file, and never put
it in the report. The script stores it in the user's config folder (outside
every project) and prints it only in masked form.

## Step 3 — Understand this request

You need a **scope**: either a sprint or a date period.

- **The user names a sprint** ("last sprint", "this sprint", "Sprint 7"): use
  `--sprint latest` or `--sprint "<name or id>"`. `latest` means the sprint
  with the most recent **start date** that has already started. The script
  finds it from the sprint lists' dates, so do not work it out from the name.
  A sprint is any ClickUp list that has a start date and a due date.
- **The user names a period** ("this week", "since the 1st"): use `--days N`
  or `--since/--until`. Turn a relative period into exact dates and repeat the
  dates in your answer, so a wrong guess is easy to see.
- **The user names neither:** check `default_scope` in `--status`. If it is
  `latest-sprint`, use the latest sprint without asking. Otherwise ask, and
  offer: the latest sprint, the last 7 days, the last 14 days, this month.
  If the user says "always do it this way", save it with
  `python scripts/setup_config.py --default-scope latest-sprint`.

When the fetch output lists `other_latest_sprints` (another space has its own
latest sprint), tell the user in one line which sprint you reported on, and
that the other one is not included. The report also lists it under "Data
notes".

**Grouping** comes from the config (default: by list). Use the user's choice
if they name one: `list`, `folder`, `space`, `tag` or `status`. If they only
say "by category", ask which of these they mean. Do this only the first time;
after that, save their choice with
`python scripts/setup_config.py --group-by <choice>`.

## Step 4 — Fetch the tasks

Write the intermediate JSON to a temporary or scratch folder, not into the
user's project:

```bash
python scripts/fetch_tasks.py --sprint latest --out <tmp>/clickup-tasks.json
# or: --sprint "Sprint 7" | --days 7 | --since YYYY-MM-DD --until YYYY-MM-DD
```

With a sprint, the script reads every task of the user in that sprint list.
With a period, it reads all open tasks assigned to the user, and the tasks
closed inside the period. It also reads the latest comments of the most relevant
tasks. Then it sorts each task into `done`, `in_progress` or `todo`, and adds
risk flags. It makes GET requests only, so nothing in ClickUp changes.

- `NOT_CONFIGURED`: go back to Step 2.
- HTTP 401/403: tell the user and stop. Do not retry with other values.
- `bad sprint`: no sprint found, or several match the name. Show the message
  to the user and ask which one they mean. Do not pick one yourself.
- All counts are 0: say so, and ask whether the period or the ids are right,
  before you build an empty report.

## Step 5 — Build and finish the report

```bash
python scripts/build_report.py <tmp>/clickup-tasks.json [--group-by tag] [--out <path>]
```

The script writes every data section. The main part is **"Tasks by sprint"**:
one section per sprint (list), newest first, and inside each sprint one
section per ClickUp status, in the workspace's own order (to do → in progress
→ review/QA → done …). Every ticket is shown under its status. With
`--group-by tag`, `space` or `folder`, that becomes the top level instead of
the sprint. The "At a glance" table counts tickets per sprint and status.

Open the file and finish it:

1. **Replace `<!-- FILL: executive-summary -->`** with 3–5 bullets: the most
   important delivery, the counts, the biggest risk, and what comes next.
   Lead with outcomes ("Shipped the barcode check on every save path"), not
   activity ("Worked on tickets").
2. **Replace `<!-- FILL: talking-points -->`** with 3–5 short bullets the user
   can say out loud. Include one bullet that asks for help or a decision when a
   blocker exists.
3. **Write every ticket summary.** Each ticket has one
   `<!-- FILL: summary <task id> -->` marker under its status. This is the part
   the user reads before the meeting, so read each ticket in full first: its
   whole `description` and all of its `recent_comments`. Then replace the
   marker with 2–4 plain sentences:
   - **What it is about**: the problem or the goal, in business terms.
   - **What happened**: what was done, found or decided. Comments often hold
     this (a fix, "no bug", a decision in a meeting).
   - **Where it stands and what is next**: for example, waiting for QA, QA
     asked for changes, or blocked by something.

   Say who did something only when a comment shows it. When the description
   and the comments are both empty, write "No description or comments in
   ClickUp." Do not build a story from the title.
4. **Check the "Blocked / at risk" table against what you read.** The script
   flags risks with keywords, so it misses risks that are phrased another way:
   for example, QA sent the ticket back with failing test cases, or a comment
   says the work cannot be tested. Comments are often not in English. For
   each open ticket where the comments show such a risk, add a row with a
   short reason and the date of the comment. Update the "At risk" count to
   match.
5. If the configured language is not English, translate the headings and your
   text. Keep task names exactly as they are in ClickUp.

Base every sentence on the JSON. If a number is not there, leave it out. Do not
estimate it. Check that no `FILL` marker is left:
`grep -n "FILL:" <report>` must print nothing.

## Step 6 — Hand it over

Reply with:
- the report path
- the counts (done, in progress, to do, at risk)
- the executive summary bullets
- any `warnings` from the fetch

Do not paste the whole report into chat; the file is the deliverable. Delete
the temporary JSON if you created it in a temp folder.

## Later changes

- New token, a different workspace, or a different person's ids: run Step 2
  again. Values you leave out are kept.
- Preferences only: `setup_config.py --language Arabic`, `--report-dir <dir>`,
  `--group-by tag` or `--default-scope latest-sprint|ask` (this does not
  re-check the token).
- Forget everything: `setup_config.py --reset`. Confirm with the user first.

For API behavior (endpoints, status types, rate limits) and troubleshooting,
read `references/clickup-api.md`.
