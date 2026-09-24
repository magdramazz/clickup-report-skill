#!/usr/bin/env python3
"""Fetch the configured user's ClickUp tasks and write normalized JSON.

Read-only: every call is a GET. The token never appears in the output.

Two scopes:
  python fetch_tasks.py --sprint latest --out tasks.json      # the latest sprint, chosen by its dates
  python fetch_tasks.py --sprint "Sprint 7" --out tasks.json  # a sprint (list) by name or id
  python fetch_tasks.py --days 7 --out tasks.json             # a date period
  python fetch_tasks.py --since 2026-09-01 --until 2026-09-30 --out tasks.json
With none of these, the saved default_scope is used ("latest-sprint"), if any.

Sprint scope: a sprint is any list with a start and a due date. "latest" is
the one with the most recent start date that has already started (a sprint
created in advance is skipped). Every task of the user in that list is
included, whatever its close date. The latest sprint of each OTHER space is
reported in scope.other_latest_sprints, so the agent can mention it.

Period scope:
  - every OPEN task assigned to the user (any age; open work is always relevant)
  - every task CLOSED/DONE inside the period
Both scopes read the latest comments of the most relevant tasks (to spot blockers).

Each task gets a bucket (done / in_progress / todo) and risk flags
(overdue, blocked, stale, over_estimate, no_due_date).
Tracked time = the user's time entries inside the period.

Exit codes: 0 ok, 2 not configured, 3 API error, 4 bad arguments.
"""
import argparse
import json
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from clickup_client import ClickUpError, emit, get, load_config, resolve_token, utf8_stdout  # noqa: E402

DONE_TYPES = {"closed", "done"}
# Some workspaces use a status named "done" whose type is "custom"; the name decides then.
DONE_NAME_RE = re.compile(r"^(done|completed?|finished|resolved|shipped|released|deployed)$", re.I)
BLOCK_RE = re.compile(
    r"\b(blocked|blocker|blocking|on hold|waiting (on|for)(?! (qa|review|testing)\b)|stuck|depends on|not working|doesn't work"
    r"|(can ?not|cannot|can't|unable to) (be )?(test|tested|proceed|continue|deploy))\b", re.I)
STALE_DAYS = 14
PAGE_SIZE = 100
HALF_DAY_MS = 12 * 3_600_000


def local_ms(day, end=False):
    dt = datetime(day.year, day.month, day.day)
    if end:
        dt += timedelta(days=1) - timedelta(milliseconds=1)
    return int(dt.astimezone().timestamp() * 1000)


def ms_to_date(ms):
    if not ms:
        return None
    return datetime.fromtimestamp(int(ms) / 1000).astimezone().strftime("%Y-%m-%d")


def one_line(text, limit):
    text = re.sub(r"\s+", " ", text or "").strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def fetch_pages(path, token, params):
    tasks, page = [], 0
    while True:
        data = get(path, token, {**params, "page": page})
        batch = data.get("tasks", [])
        tasks.extend(batch)
        last = data.get("last_page")
        if not batch or last is True or (last is None and len(batch) < PAGE_SIZE) or page >= 200:
            return tasks
        page += 1


def normalize(t, spaces):
    status = t.get("status") or {}
    folder = t.get("folder") or {}
    closed_ms = int(t.get("date_closed") or t.get("date_done") or 0)
    return {
        "id": t.get("id"),
        "custom_id": t.get("custom_id"),
        "name": one_line(t.get("name"), 200),
        "url": t.get("url"),
        "status": status.get("status", ""),
        "status_type": status.get("type", ""),
        "status_order": int(status.get("orderindex") or 0),  # ClickUp's order: to do → … → closed
        "space": spaces.get(str((t.get("space") or {}).get("id"))),
        "folder": None if folder.get("hidden") else folder.get("name"),
        "list": (t.get("list") or {}).get("name"),
        "priority": (t.get("priority") or {}).get("priority"),
        "tags": [x.get("name") for x in t.get("tags") or [] if x.get("name")],
        "is_subtask": bool(t.get("parent")),
        "points": t.get("points"),
        "estimate_ms": int(t.get("time_estimate") or 0),
        "tracked_ms": 0,  # filled from time entries: time the user tracked inside the period
        "created": ms_to_date(t.get("date_created")),
        "updated": ms_to_date(t.get("date_updated")),
        "start": ms_to_date(t.get("start_date")),
        "due": ms_to_date(t.get("due_date")),
        "closed": ms_to_date(closed_ms),
        "description": one_line(t.get("text_content"), 4000),
        "updated_ms": int(t.get("date_updated") or 0),
        "due_ms": int(t.get("due_date") or 0),
        "closed_ms": closed_ms,
    }


def find_sprints(team_id, token):
    """Every list with a start and a due date, in every space (folder lists and folderless lists)."""
    sprints = []
    for space in get(f"/team/{team_id}/space", token, {"archived": "false"}).get("spaces", []):
        lists = []
        for folder in get(f"/space/{space['id']}/folder", token, {"archived": "false"}).get("folders", []):
            lists += [(folder.get("name"), lst) for lst in folder.get("lists", [])]
        folderless = get(f"/space/{space['id']}/list", token, {"archived": "false"}).get("lists", [])
        lists += [(None, lst) for lst in folderless]
        for folder_name, lst in lists:
            if lst.get("start_date") and lst.get("due_date"):
                sprints.append({"id": str(lst["id"]), "name": lst.get("name"), "folder": folder_name,
                                "space": space.get("name"), "start_ms": int(lst["start_date"]),
                                "due_ms": int(lst["due_date"])})
    return sprints


def pick_sprint(sprints, wanted, now_ms):
    """Return (sprint, other_latest_sprints). Raise ValueError with a message for the user."""
    def day(ms):
        # Sprint bounds are midnight / end of day in the workspace's time zone, which may differ
        # from this machine's. Moving 12 hours into the day gives the right calendar date in any zone.
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")

    def public(s):
        return {"id": s["id"], "name": s["name"], "folder": s["folder"], "space": s["space"],
                "start": day(s["start_ms"] + HALF_DAY_MS), "due": day(s["due_ms"] - HALF_DAY_MS)}

    if wanted.lower() == "latest":
        started = [s for s in sprints if s["start_ms"] <= now_ms]
        if not started:
            raise ValueError("no started sprint found (no list has a start date in the past)")
        chosen = max(started, key=lambda s: s["start_ms"])
        latest_per_space = {}
        for s in started:
            best = latest_per_space.get(s["space"])
            if s["space"] != chosen["space"] and (best is None or s["start_ms"] > best["start_ms"]):
                latest_per_space[s["space"]] = s
        return public(chosen), [public(s) for s in latest_per_space.values()]
    matches = [s for s in sprints if s["id"] == wanted]
    matches = matches or [s for s in sprints if wanted.lower() in (s["name"] or "").lower()]
    if not matches:
        raise ValueError(f"no sprint list matches {wanted!r}")
    if len(matches) > 1:
        raise ValueError("several sprints match: " + "; ".join(
            f"{s['id']} {s['name']} ({s['space']})" for s in matches) + ". Use the id.")
    return public(matches[0]), []


def classify(task, since_ms, until_ms, now_ms):
    """Return the bucket, or None when the task is outside the report."""
    if task["status_type"] in DONE_TYPES or DONE_NAME_RE.match(task["status"].strip()):
        ref = task["closed_ms"] or task["updated_ms"]
        return "done" if since_ms <= ref <= until_ms else None
    return "todo" if task["status_type"] == "open" else "in_progress"


def flag(task, now_ms):
    flags = []
    open_task = task["bucket"] != "done"
    if open_task and task["due_ms"] and task["due_ms"] < now_ms:
        flags.append("overdue")
    if open_task and not task["due_ms"]:
        flags.append("no_due_date")
    if open_task and BLOCK_RE.search(task["status"]):
        flags.append("blocked")
    if task["bucket"] == "in_progress" and task["updated_ms"] < now_ms - STALE_DAYS * 86_400_000:
        flags.append("stale")
    return flags


def add_tracked_time(tasks, team_id, user_id, token, since_ms, until_ms, warnings):
    """Task objects carry no tracked time, so sum the user's time entries in the period."""
    try:
        entries = get(f"/team/{team_id}/time_entries", token, {
            "start_date": since_ms, "end_date": until_ms, "assignee": user_id}).get("data", [])
    except ClickUpError as err:
        warnings.append(f"tracked time unavailable: {err}")
        return
    per_task = {}
    for entry in entries:
        task_id = (entry.get("task") or {}).get("id") if isinstance(entry.get("task"), dict) else None
        duration = int(entry.get("duration") or 0)
        if task_id and duration > 0:  # a running timer has a negative duration
            per_task[task_id] = per_task.get(task_id, 0) + duration
    for task in tasks:
        task["tracked_ms"] = per_task.get(task["id"], 0)
    if not entries:
        warnings.append("no time entries in this period (time tracking not used, so tracked time shows —)")


def add_comments(tasks, token, limit, warnings):
    order = {"in_progress": 0, "todo": 1, "done": 2}
    picked = sorted(tasks, key=lambda t: (order[t["bucket"]], -t["updated_ms"]))[:limit]
    for task in picked:
        try:
            comments = get(f"/task/{task['id']}/comment", token).get("comments", [])
        except ClickUpError as err:
            warnings.append(f"comments skipped for {task['id']}: {err}")
            continue
        comments = sorted(comments, key=lambda c: int(c.get("date") or 0), reverse=True)[:5]
        task["recent_comments"] = [{
            "by": (c.get("user") or {}).get("username"),
            "date": ms_to_date(c.get("date")),
            "text": one_line(c.get("comment_text"), 600),
        } for c in comments]
        if task["bucket"] != "done" and "blocked" not in task["flags"] and any(
                BLOCK_RE.search(c["text"]) for c in task["recent_comments"]):
            task["flags"].append("blocked_in_comment")
    if len(tasks) > limit:
        warnings.append(f"comments read for {limit} of {len(tasks)} tasks (raise --max-comment-tasks for more)")


def main():
    utf8_stdout()
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--sprint", help='"latest" or a sprint (list) name or id')
    p.add_argument("--days", type=int, help="period = the last N days, today included")
    p.add_argument("--since", help="YYYY-MM-DD (local time)")
    p.add_argument("--until", help="YYYY-MM-DD (local time, default today)")
    p.add_argument("--out", required=True, help="where to write the JSON")
    p.add_argument("--max-comment-tasks", type=int, default=40, help="0 turns comments off")
    args = p.parse_args()

    cfg = load_config()
    token = resolve_token(cfg)
    if not cfg or not token or not cfg.get("user_id") or not cfg.get("team_id"):
        emit({"ok": False, "error": "NOT_CONFIGURED", "hint": "run setup_config.py first"})
        return 2

    now_ms = int(datetime.now().timestamp() * 1000)
    team_id, user_id = cfg["team_id"], cfg["user_id"]
    warnings = []
    sprint_arg = args.sprint
    if not (sprint_arg or args.days or args.since):
        if cfg.get("default_scope") == "latest-sprint":
            sprint_arg = "latest"
        else:
            emit({"ok": False, "error": "bad scope: give --sprint, --days or --since"})
            return 4

    scope = {"type": "period"}
    try:
        spaces = {}
        try:
            spaces = {str(s["id"]): s.get("name") for s in get(f"/team/{team_id}/space", token).get("spaces", [])}
        except ClickUpError as err:
            warnings.append(f"space names unavailable: {err}")
        base = {"assignees[]": [user_id], "subtasks": "true"}

        if sprint_arg:
            try:
                sprint, others = pick_sprint(find_sprints(team_id, token), sprint_arg, now_ms)
            except ValueError as err:
                emit({"ok": False, "error": f"bad sprint: {err}"})
                return 4
            scope = {"type": "sprint", "sprint": sprint, "other_latest_sprints": others}
            since, until = date.fromisoformat(sprint["start"]), date.fromisoformat(sprint["due"])
            raw = fetch_pages(f"/list/{sprint['id']}/task", token, {**base, "include_closed": "true"})
            # Everything in the sprint list belongs to the sprint, whatever its close date.
            classify_since, classify_until = float("-inf"), float("inf")
        else:
            try:
                until = date.fromisoformat(args.until) if args.until else date.today()
                since = date.fromisoformat(args.since) if args.since else until - timedelta(days=args.days - 1)
                if since > until:
                    raise ValueError("--since is after --until")
            except ValueError as err:
                emit({"ok": False, "error": f"bad period: {err}"})
                return 4
            classify_since, classify_until = local_ms(since), local_ms(until, end=True)
            raw = fetch_pages(f"/team/{team_id}/task", token, {**base, "include_closed": "false"})
            # Closed tasks: only those touched since the period start; classify() filters by close date.
            raw += fetch_pages(f"/team/{team_id}/task", token,
                               {**base, "include_closed": "true", "date_updated_gt": classify_since})
    except ClickUpError as err:
        emit({"ok": False, "error": str(err)})
        return 3
    since_ms, until_ms = local_ms(since), local_ms(until, end=True)

    tasks, seen = [], set()
    for t in raw:
        if t.get("id") in seen or t.get("archived"):
            continue
        seen.add(t.get("id"))
        task = normalize(t, spaces)
        task["bucket"] = classify(task, classify_since, classify_until, now_ms)
        if task["bucket"]:
            task["flags"] = flag(task, now_ms)
            tasks.append(task)

    add_tracked_time(tasks, team_id, user_id, token, since_ms, until_ms, warnings)
    for task in tasks:
        if task["estimate_ms"] and task["tracked_ms"] > task["estimate_ms"]:
            task["flags"].append("over_estimate")

    if args.max_comment_tasks > 0:
        add_comments(tasks, token, args.max_comment_tasks, warnings)

    counts = {b: sum(1 for t in tasks if t["bucket"] == b) for b in ("done", "in_progress", "todo")}
    result = {
        "meta": {
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "period": {"since": since.isoformat(), "until": until.isoformat()},
            "scope": scope,
            "user": {"id": user_id, "name": cfg.get("user_name")},
            "workspace": {"id": team_id, "name": cfg.get("team_name")},
            "language": cfg.get("language", "English"),
            "group_by": cfg.get("group_by", "list"),
            "report_dir": cfg.get("report_dir"),
            "counts": counts,
            "warnings": warnings,
        },
        "tasks": tasks,
    }
    out = Path(args.out).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    emit({"ok": True, "out": str(out), "scope": scope, "period": result["meta"]["period"],
          "counts": counts, "warnings": warnings})
    return 0


if __name__ == "__main__":
    sys.exit(main())
