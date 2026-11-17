"""Turn a plan (tasks, what each waits for, milestones) into CUBIC to-dos, in topological order.

    python3 dev/plan_tools.py PLAN.json                  # check the plan, print the build order
    python3 dev/plan_tools.py PLAN.json --write OUT.json # also write a CUBIC import file

PLAN.json describes the plan:

    {"name": "...", "idPrefix": "bs-", "by": "claude", "baseAt": <ms>, "milestoneCell": "f1-c1",
     "tasks": [{"key": "01", "cell": "f2-c0", "text": "01 ...", "days": 1, "after": [], "who": "vida"}, ...],
     "milestones": [{"key": "M1", "text": "11/20 · M1 ...", "after": ["01", ...]}, ...],
     "check": {"layers": [["01", ...], ...], "criticalDays": 18.5, "impliedLinks": 8}}

The order of "tasks" breaks ties: among tasks that are ready at the same time, the one listed
first comes first. "check" is optional; each key in it is compared and the script stops if the
plan disagrees (layers of the tasks, length of the critical path in days, number of "after"
links already implied by others).

OUT.json is a normal CUBIC export: import it from "Data" › "Import". Import adds or updates,
never deletes, so importing it twice changes nothing. Each to-do gets a timestamp that falls with
its place in the build order, so every space lists its tasks top to bottom in build order.

A team's real plan is business data: keep it in data/ (not tracked by git) or in the workspace
itself, not in this public repository. tests/fixtures/plan-shape.json is a neutral example.
"""
import json
import sys
from collections import defaultdict, deque
from pathlib import Path


def load(path):
    plan = json.loads(Path(path).read_text(encoding="utf-8"))
    nodes, rank = {}, {}
    for t in plan["tasks"]:
        nodes[t["key"]] = {"cell": t["cell"], "text": t["text"], "days": float(t.get("days", 0)), "after": list(t.get("after", [])), "task": True, "who": t.get("who", "")}
    for m in plan.get("milestones", []):
        nodes[m["key"]] = {"cell": plan.get("milestoneCell", "f1-c1"), "text": m["text"], "days": 0.0, "after": list(m.get("after", [])), "task": False, "who": ""}
    for k, key in enumerate([t["key"] for t in plan["tasks"]] + [m["key"] for m in plan.get("milestones", [])]):
        if key in rank:
            raise SystemExit(f"{key} appears twice")
        rank[key] = k
    return plan, nodes, rank


def kahn(nodes, rank):
    """Kahn's algorithm. Returns (order, layer). Stops on a missing name or a loop.
    layer[k] = the longest chain of "after" links ending at k (0 = waits for nothing)."""
    for k, n in nodes.items():
        for d in n["after"]:
            if d not in nodes:
                raise SystemExit(f"{k} waits for {d}, which is not in the plan")
            if d == k:
                raise SystemExit(f"{k} waits for itself")
    indeg = {k: len(set(n["after"])) for k, n in nodes.items()}
    users = defaultdict(list)
    for k, n in nodes.items():
        for d in set(n["after"]):
            users[d].append(k)
    queue = deque(sorted((k for k, v in indeg.items() if v == 0), key=rank.get))
    order, layer = [], {k: 0 for k in queue}
    while queue:
        k = queue.popleft()
        order.append(k)
        for u in users[k]:
            layer[u] = max(layer.get(u, 0), layer[k] + 1)
            indeg[u] -= 1
            if indeg[u] == 0:
                queue.append(u)
        queue = deque(sorted(queue, key=rank.get))
    if len(order) != len(nodes):
        stuck = sorted(set(nodes) - set(order), key=rank.get)
        raise SystemExit(f"Dependency loop among: {', '.join(stuck)}")
    return order, layer


def implied_links(nodes):
    """Links d -> k where another need of k already waits for d (left out of a drawing)."""
    reach = {}

    def up(k):
        if k not in reach:
            s = set()
            for d in nodes[k]["after"]:
                s.add(d)
                s |= up(d)
            reach[k] = s
        return reach[k]

    return [(d, k) for k, n in nodes.items() for d in n["after"] if any(d in up(o) for o in n["after"] if o != d)]


def critical_path(nodes, order):
    finish, prev = {}, {}
    for k in order:
        start = 0.0
        for d in nodes[k]["after"]:
            if finish[d] > start:
                start, prev[k] = finish[d], d
        finish[k] = start + nodes[k]["days"]
    end = max(nodes, key=lambda k: finish[k])
    chain = [end]
    while chain[-1] in prev:
        chain.append(prev[chain[-1]])
    return finish[end], list(reversed(chain))


def build(plan, nodes, rank):
    """Check the plan and return (report lines, CUBIC items in build order)."""
    order, layer = kahn(nodes, rank)
    tasks = {k: n for k, n in nodes.items() if n["task"]}
    _, task_layer = kahn(tasks, rank)
    report = [f"{len(nodes)} to-dos ({len(tasks)} tasks, {len(nodes) - len(tasks)} milestones), no loops."]
    check = plan.get("check", {})
    if "layers" in check:
        got = defaultdict(list)
        for k, l in task_layer.items():
            got[l].append(k)
        computed = [sorted(got[l], key=rank.get) for l in range(max(got) + 1)]
        expected = [sorted(l, key=rank.get) for l in check["layers"]]
        if computed != expected:
            for i in range(max(len(computed), len(expected))):
                a = computed[i] if i < len(computed) else []
                b = expected[i] if i < len(expected) else []
                if a != b:
                    report.append(f"layer {i}: computed {a}, expected {b}")
            raise SystemExit("\n".join(report + ["Layers do not match"]))
        report.append(f"Layers match: {len(computed)} layers of tasks.")
    days, chain = critical_path(tasks, [k for k in order if k in tasks])
    if "criticalDays" in check and abs(days - float(check["criticalDays"])) > 1e-9:
        raise SystemExit(f"Critical path is {days} days, expected {check['criticalDays']}")
    report.append(f"Critical path {days:g} days: {' > '.join(chain)}")
    implied = implied_links(tasks)
    if "impliedLinks" in check and len(implied) != int(check["impliedLinks"]):
        raise SystemExit(f"{len(implied)} implied links, expected {check['impliedLinks']}: {implied}")
    report.append(f"Implied links (not drawn): {', '.join(f'{a}>{b}' for a, b in implied) or 'none'}")
    prefix, by, base = plan.get("idPrefix", "plan-"), plan.get("by", "claude"), int(plan["baseAt"])
    doc = lambda key: prefix + key.lower()
    items = []
    for i, k in enumerate(order):
        n, at = nodes[k], base - i * 1000
        if n["who"] not in ("", "vida", "anand", "obama"):
            raise SystemExit(f"{k}: unknown owner {n['who']}")
        items.append({"id": doc(k), "cell": n["cell"], "text": n["text"], "url": "", "who": n["who"], "done": False,
                      "deps": [doc(d) for d in n["after"]], "by": by, "at": at, "editedBy": by, "editedAt": at})
        report.append(f"  {i + 1:2d}. L{layer[k]:<2d} {n['text']}")
    return report, items


def export(items, when="2026-11-17T00:00:00.000Z"):
    return {"app": "cubic", "version": 1, "exportedAt": when, "items": items}


def main(argv):
    if len(argv) < 2:
        raise SystemExit(__doc__)
    plan, nodes, rank = load(argv[1])
    report, items = build(plan, nodes, rank)
    print(plan.get("name", argv[1]))
    print("\n".join(report))
    if "--write" in argv:
        out = Path(argv[argv.index("--write") + 1])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(export(items), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"Wrote {out} ({len(items)} to-dos).")


if __name__ == "__main__":
    main(sys.argv)
