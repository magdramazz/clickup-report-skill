#!/usr/bin/env python3
"""Render the Markdown report from the JSON written by fetch_tasks.py.

  python build_report.py tasks.json                  # writes to the saved report_dir
  python build_report.py tasks.json --out report.md --group-by tag

Layout: summary sections first, then "Tasks by sprint": one section per list
(sprint), and inside it one section per ClickUp status, in the workspace's own
status order (to do → in progress → review/QA → done …). Every ticket gets a
facts line and a summary marker.

The agent fills these markers after reading the tickets:
  <!-- FILL: executive-summary -->
  <!-- FILL: talking-points -->
  <!-- FILL: summary <task id> -->     (one per ticket)
"""
import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from clickup_client import emit, utf8_stdout  # noqa: E402

RISK_FLAGS = {
    "overdue": "overdue",
    "blocked": "status says blocked / on hold",
    "blocked_in_comment": "a recent comment mentions a blocker",
    "stale": "no update for 14+ days",
    "over_estimate": "time tracked this period is over the estimate",
}
GROUP_LABELS = {"list": "sprint / list", "folder": "folder", "space": "space", "tag": "tag", "status": "status"}


def cell(text):
    return (str(text) if text not in (None, "") else "—").replace("|", "\\|").replace("\n", " ")


def duration(ms):
    if not ms:
        return "—"
    minutes = round(ms / 60000)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m" if hours else f"{minutes}m"


def link(task):
    label = f"{task['custom_id']} {task['name']}" if task.get("custom_id") else task["name"]
    return f"[{cell(label)}]({task['url']})" if task.get("url") else cell(label)


def natural(text):
    """Sort key where "Sprint 10" comes after "Sprint 9"."""
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", text or "")]


def group_key(task, group_by):
    if group_by == "list":
        return task.get("list") or "(no list)"
    if group_by == "folder":
        return task.get("folder") or "(no folder)"
    if group_by == "space":
        return task.get("space") or "(no space)"
    if group_by == "tag":
        return task["tags"][0] if task.get("tags") else "(untagged)"
    return task.get("status") or "(no status)"


def table(headers, rows):
    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    lines += ["| " + " | ".join(row) + " |" for row in rows]
    return "\n".join(lines)


def status_order(tasks):
    """Status names in ClickUp's own order (the lowest orderindex seen for each name)."""
    rank = {}
    for t in tasks:
        name = t.get("status") or "(no status)"
        rank[name] = min(rank.get(name, 1e9), t.get("status_order", 1e9))
    return sorted(rank, key=lambda n: (rank[n], n.lower()))


def ticket_block(task):
    facts = [f"**Status:** {cell(task['status'])}", f"**Priority:** {cell(task.get('priority'))}",
             f"**Due:** {cell(task['due'])}"]
    if task["bucket"] == "done":
        facts.append(f"**Closed:** {cell(task['closed'])}")
    if task["estimate_ms"] or task["tracked_ms"]:
        facts.append(f"**Tracked / estimate:** {duration(task['tracked_ms'])} / {duration(task['estimate_ms'])}")
    if task.get("points") not in (None, ""):
        facts.append(f"**Points:** {task['points']}")
    return [f"##### {link(task)}\n", " · ".join(facts) + "\n", f"<!-- FILL: summary {task['id']} -->\n"]


def render(data, group_by):
    meta, tasks = data["meta"], data["tasks"]
    done = [t for t in tasks if t["bucket"] == "done"]
    doing = [t for t in tasks if t["bucket"] == "in_progress"]
    todo = [t for t in tasks if t["bucket"] == "todo"]
    risky = [t for t in tasks if t["bucket"] != "done" and any(f in RISK_FLAGS for f in t["flags"])]
    undated = sum(1 for t in tasks if "no_due_date" in t["flags"])
    statuses = status_order(tasks)

    groups = {}
    for t in tasks:
        groups.setdefault(group_key(t, group_by), []).append(t)
    # Sprints and folders: newest first. Everything else: A→Z.
    names = sorted(groups, key=natural, reverse=group_by in ("list", "folder"))

    def points(ts):
        return sum(float(t["points"]) for t in ts if t.get("points") not in (None, ""))

    user = meta["user"].get("name") or meta["user"]["id"]
    period = meta["period"]
    scope = meta.get("scope") or {"type": "period"}
    workspace = cell(meta["workspace"].get("name") or meta["workspace"]["id"])
    if scope["type"] == "sprint":
        sprint = scope["sprint"]
        where = " / ".join(cell(x) for x in (sprint.get("space"), sprint.get("folder")) if x)
        out = [
            f"# Sprint report — {cell(user)}\n",
            f"**Sprint:** {cell(sprint['name'])} ({sprint['start']} → {sprint['due']}) · **Where:** {where} · "
            f"**Workspace:** {workspace} · **Generated:** {meta['generated_at']}\n",
        ]
    else:
        out = [
            f"# Work report — {cell(user)}\n",
            f"**Period:** {period['since']} → {period['until']} · **Workspace:** {workspace} · "
            f"**Generated:** {meta['generated_at']}\n",
        ]
    out += [
        "## Executive summary\n",
        "<!-- FILL: executive-summary -->\n",
        "## At a glance\n",
        table(["Done", "In progress", "To do", "At risk"],
              [[str(len(done)), str(len(doing)), str(len(todo)), str(len(risky))]]) + "\n",
    ]

    if group_by != "status":
        rows = []
        for name in names:
            members = groups[name]
            rows.append([cell(name)] + [str(sum(1 for t in members if t["status"] == s) or "·") for s in statuses]
                        + [str(len(members))])
        out.append(table([GROUP_LABELS[group_by].capitalize()] + [cell(s) for s in statuses] + ["Total"], rows) + "\n")

    out.append(table(["Metric", "Value"], [
        ["Time tracked in period (all tasks)", duration(sum(t["tracked_ms"] for t in tasks))],
        ["Estimate of open tasks", duration(sum(t["estimate_ms"] for t in doing + todo))],
        ["Sprint points done / open", f"{points(done):g} / {points(doing + todo):g}"],
        ["Open tasks with no due date", str(undated)],
    ]) + "\n")

    out.append(f"## Blocked / at risk ({len(risky)})\n")
    out.append(table(["Task", "Why", "Status", "Where"], [
        [link(t), cell("; ".join(RISK_FLAGS[f] for f in t["flags"] if f in RISK_FLAGS)),
         cell(t["status"]), cell(group_key(t, group_by))] for t in risky]) + "\n" if risky else "_Nothing at risk._\n")

    out.append("## Talking points\n")
    out.append("<!-- FILL: talking-points -->\n")

    out.append(f"## Tasks by {GROUP_LABELS[group_by]}\n")
    for name in names:
        members = groups[name]
        out.append(f"### {cell(name)} ({len(members)})\n")
        folders = sorted({t["folder"] for t in members if t.get("folder")})
        if group_by == "list" and folders:
            out.append(f"_Folder: {cell(', '.join(folders))}_\n")
        if group_by == "status":
            for t in members:
                out.extend(ticket_block(t))
            continue
        for status in statuses:
            in_status = [t for t in members if t["status"] == status]
            if not in_status:
                continue
            out.append(f"#### {cell(status[:1].upper() + status[1:])} ({len(in_status)})\n")
            for t in in_status:
                out.extend(ticket_block(t))

    notes = list(meta.get("warnings") or [])
    for other in scope.get("other_latest_sprints") or []:
        notes.append(f"Not included: the latest sprint of space {other['space']} is {other['name']} "
                     f"({other['start']} → {other['due']}).")
    if notes:
        out.append("## Data notes\n")
        out.append("\n".join(f"- {cell(n)}" for n in notes) + "\n")
    return "\n".join(out)


def main():
    utf8_stdout()
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("tasks_json")
    p.add_argument("--out", help="default: <report_dir>/clickup-report-<until>.md")
    p.add_argument("--group-by", choices=tuple(GROUP_LABELS))
    args = p.parse_args()

    data = json.loads(Path(args.tasks_json).read_text(encoding="utf-8"))
    meta = data["meta"]
    group_by = args.group_by or meta.get("group_by") or "list"
    if args.out:
        out = Path(args.out).expanduser()
    else:
        out = Path(meta.get("report_dir") or ".").expanduser() / f"clickup-report-{meta['period']['until']}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(data, group_by), encoding="utf-8")
    emit({"ok": True, "out": str(out), "group_by": group_by, "counts": meta["counts"],
          "fill_markers": ["executive-summary", "talking-points", "summary <task id> (one per ticket)"]})
    return 0


if __name__ == "__main__":
    sys.exit(main())
