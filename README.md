# CUBIC

A shared workspace for the Best-shot team, shown as a cube you can turn: six
sides, four spaces per side, 24 places to think and decide together.

Live page (the team's shared data): https://claude.ai/artifact/VXX9JmKG23SPdy8pm1ZMSp
(private to its owner until shared from the page's Share menu)

Code: https://github.com/Ulziibadrakh-tech/cubic

The same `index.html` is also deployed on Vercel. There it has no Claude
sign-in or shared database, so it keeps data **in that browser only** and says
so on screen. Use "Өгөгдөл" (export/import) to move data between the two.

## What it does today

The screen is in Mongolian, with an EN button for English (each viewer's
choice is kept in their browser) and a button for light or dark theme.

- **Cube and list.** Drag the cube freely; it carries on with the speed of the
  drag and settles on the nearest side. Arrows and keyboard work too.
  "Жагсаалт" shows the same 24 spaces without 3D. Links such as `#f0-c2` open
  a space directly.
- **Spaces are to-do lists.** Type a line and press Enter. Tick it when done,
  tap the text to edit, tap the round badge to set who owns it, swipe the row
  sideways to delete (with undo). The ✕ button does the same without a swipe.
- **People.** The team is Vida, Anand and Obama. Each person signs in with
  their own Claude account and picks who they are on first visit. Vida and
  Anand build the technology (Anand as forward deployed engineer, with the
  restaurants); Obama runs business, marketing and branding. A new to-do starts
  with its side's owner (Pilots → Anand, Technology → Vida, Marketing,
  Business & Finance and Branding → Obama); one tap on the badge changes it.
- **Time.** A person's timer starts when they open the workspace and stops on
  "Түр зогсоох". Time is attributed to the space they are in. After more than
  10 minutes away the page asks whether to count that time. "Баг" shows each
  person's status, open to-dos, today and the last 7 days.

- **Reorganise.** Tap a to-do's text to edit it or move it to another space
  (with undo). Paste several lines into "Шинэ ажил" to add one to-do per line;
  list bullets and numbers are stripped.
- **Data (Өгөгдөл).** Export everything (sides, to-dos, people, time) as JSON,
  or the to-dos as CSV (with a `deps` column). Inside Claude the file is saved
  through the page's download permission; on a phone the share sheet offers
  Proton Drive. "Хуулах" (copy) always works. Import takes a JSON export or a
  CSV with at least `cell` and `text` columns, shows what will change, and
  writes only after you confirm. Import never deletes: a to-do with the same
  id is replaced only if the imported one was edited later; a CSV without a
  `deps` column leaves dependencies alone; a dependency that would close a
  loop is left out and counted; rows aimed at the two view spaces are skipped
  and counted; people and time records are added only if missing (an open
  timer in them is closed); importing the same file twice adds nothing.

No deadlines, priorities or permissions beyond the page's own sharing. That is
on purpose for a team of three.

## Layout of this folder

| Path | What it is |
|---|---|
| `index.html` | The whole product: markup, styles and script in one file. This is what gets published. |
| `dev/serve.py` | Local server. Serves `index.html` with stand-in data so it runs without Claude. |
| `dev/fake-claude.js` | The stand-in for sign-in and shared data. Stores data in the browser's localStorage. |
| `tests/check_flow.py` | Phone-sized browser check of the main flows (120 checks). |
| `dev/plan_tools.py` | Turns a plan (tasks, what each waits for, milestones) into a CUBIC import file in topological order, and checks it. |
| `tests/fixtures/plan-shape.json` | A neutral 41-task plan with a real shape, used by the checks. |
| `data/` | Real plans and backups. Not tracked by git and not deployed: this repository is public. |
| `.github/workflows/check.yml` | CI: runs the plan tool and `tests/check_flow.py` on every push and pull request. |
| `vercel.json`, `.vercelignore` | Static Vercel deploy: serves `index.html` only. |
| `docs/SPEC.md` | The original CUBIC master prompt, kept as the long-term reference. |
| `docs/ROADMAP.md` | What is built against that spec, and what comes next. |
| `CLAUDE.md` | Working rules for a Claude session that edits this project. |

## Run it locally

Needs Python 3 only.

```
py dev/serve.py          # Windows
python3 dev/serve.py     # macOS / Linux
```

Open http://localhost:8800/ . Local data is separate from the team's real data.

- Second person: http://localhost:8800/?as=second
- Read-only view: http://localhost:8800/?readonly=1
- Clear local data: http://localhost:8800/?reset=1
- As on Vercel (no stand-in, browser-only data): http://localhost:8800/?bare=1

## Check it

```
pip install playwright
playwright install chromium
py dev/serve.py                 # keep running in one window
py tests/check_flow.py          # in another
```

## Publish a change

The live page is a Claude artifact. Its code is `index.html`; its data lives in
the artifact's own database and is not in this folder.

Ask Claude, in a session with this folder connected: "publish index.html to
the CUBIC artifact". Claude republishes the file to the same page; the data and
the link stay as they are. See `CLAUDE.md` for the exact identifiers.

Vercel deploys from GitHub on every push to `main` (once the repo is linked
in Vercel). It's a static site: no build step, framework preset "Other".
`vercel.json` sets a Content-Security-Policy (only Google Fonts besides the
page itself), no framing, and no camera, microphone or location. The local
server applies the same headers to `?bare=1`, and a check fails if the policy
blocks anything the page uses.

## Where the data lives

Four collections in the artifact's database:

| Path | One document per | Fields |
|---|---|---|
| `faces/f0` to `faces/f5` | side of the cube | `title`, `cells` (four names), `editedBy`, `editedAt` |
| `items/<id>` | to-do | `cell` (`f0-c2`), `text`, `url`, `who` (`vida`, `anand`, `obama` or empty), `done`, `deps` (ids it waits for), `by`, `at`, `editedBy`, `editedAt` |
| `people/<person id>` | signed-in person | `who` (which team member they are), `at` |
| `clock/<person id>` | signed-in person | `days` (`{date: {ms, bySpace}}`), `openSince`, `openCell`, `leftAt`, `paused` |

A side that has never been renamed has no document; the page falls back to the
names in `DEFAULT_FACES`. Times are milliseconds since 1970, UTC. Day keys use
the viewer's local date.

## Known limits

- Sign-in is the Claude account. The page cannot have its own passwords.
- A person with the page open on two devices at once can get a skewed timer.
- The timer measures "timer running", not proven work.
- Anyone with Contributor access can edit any entry, including another
  person's timer document. Fine for three people who trust each other.
- Not yet tested with two real people editing at the same moment. Edits to a
  to-do change only the fields touched (a merge), so two people changing
  different things on the same to-do both keep their change.

## Plan side: dependencies, graph, timeline

- Edit a to-do and use "Хүлээж буй ажил" to say what it waits for.
- `Plan › Dependency graph` sorts every linked to-do topologically (Kahn's
  algorithm). Each column is a layer ("Давхарга"): a to-do sits one layer to
  the right of the latest thing it waits for. Only direct needs are drawn; a
  need already implied by another is counted under the graph. Tap a node to
  see its chain. "Барих дараалал" lists the whole build order, numbered; below
  it are "can start now" and "blocked". A loop of waiting (possible only when
  two people edit at once) is named with a warning, never hidden.
- When the workspace holds more than one separate plan, a row of buttons picks
  which one the graph and the timeline show (the most recently added first),
  with "Бүгд" for all.
- `Plan › Milestones` holds the milestones (start the text with `MM/DD`, or
  `MM/DD/YYYY`). `Plan › Timeline` draws an SVG chart on a dark "screen":
  how many tasks the milestones up to each date need (the plan), a line for
  today, and a dot for what is done now, with a sentence saying whether that is
  on plan. Below it, one card per milestone with a progress ring, the days left
  or late, and the to-dos that can start, in build order. The Milestones space
  shows the same chart above its list. Everything on the chart comes from the
  data; nothing stores when a to-do was ticked, so there is no invented history.
- The graph and the plan picker are worked out again from the data on every
  change: when someone links or unlinks to-dos, ticks one, or adds one, every
  open page redraws with the new order, layers and plans.

## Load a plan

Write the plan as a JSON definition (see the top of `dev/plan_tools.py`, and
`tests/fixtures/plan-shape.json` for the shape), keep it in `data/`, then:

```
python3 dev/plan_tools.py data/my-plan.json --write data/my-plan.cubic.json
```

The tool refuses a plan with a missing name or a loop, can check the layers,
critical path and implied links against what the plan document says, and
writes a normal CUBIC export. Import it from "Өгөгдөл" › "Оруулах", or ask
Claude to write it into the artifact's database. Each space then lists its
tasks in build order.
