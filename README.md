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

The screen is in Mongolian.

- **Cube and list.** Drag the cube freely; it carries on with the speed of the
  drag and settles on the nearest side. Arrows and keyboard work too.
  "Жагсаалт" shows the same 24 spaces without 3D. Links such as `#f0-c2` open
  a space directly.
- **Spaces are to-do lists.** Type a line and press Enter. Tick it when done,
  tap the text to edit, tap the round badge to set who owns it, swipe the row
  sideways to delete (with undo). The ✕ button does the same without a swipe.
- **People.** The team is Vida, Anand and Obama. Each person signs in with
  their own Claude account and picks who they are on first visit.
- **Time.** A person's timer starts when they open the workspace and stops on
  "Түр зогсоох". Time is attributed to the space they are in. After more than
  10 minutes away the page asks whether to count that time. "Баг" shows each
  person's status, open to-dos, today and the last 7 days.

- **Reorganise.** Tap a to-do's text to edit it or move it to another space
  (with undo). Paste several lines into "Шинэ ажил" to add one to-do per line;
  list bullets and numbers are stripped.
- **Data (Өгөгдөл).** Export everything (sides, to-dos, people, time) as JSON,
  or the to-dos as CSV. Inside Claude, downloads are blocked, so export is
  "Хуулах" (copy). Import takes a JSON export or a CSV with at least `cell`
  and `text` columns, shows what will change, and writes only after you
  confirm. Import never deletes: a to-do with the same id is replaced only if
  the imported one was edited later; people and time records are added only
  if missing; importing the same file twice adds nothing.

No deadlines, priorities or permissions beyond the page's own sharing. That is
on purpose for a team of three.

## Layout of this folder

| Path | What it is |
|---|---|
| `index.html` | The whole product: markup, styles and script in one file. This is what gets published. |
| `dev/serve.py` | Local server. Serves `index.html` with stand-in data so it runs without Claude. |
| `dev/fake-claude.js` | The stand-in for sign-in and shared data. Stores data in the browser's localStorage. |
| `tests/check_flow.py` | Phone-sized browser check of the main flows (42 checks). |
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

## Where the data lives

Four collections in the artifact's database:

| Path | One document per | Fields |
|---|---|---|
| `faces/f0` to `faces/f5` | side of the cube | `title`, `cells` (four names), `editedBy`, `editedAt` |
| `items/<id>` | to-do | `cell` (`f0-c2`), `text`, `url`, `who` (`vida`, `anand`, `obama` or empty), `done`, `by`, `at`, `editedBy`, `editedAt` |
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
- Not yet tested with two real people editing at the same moment.

## Plan side: dependencies, graph, timeline

- Edit a to-do and use "Хүлээж буй ажил" to say what it waits for.
- `Plan › Dependency graph` draws every linked to-do left to right and counts
  done, open, blocked and blocking. Tap a node to see its chain. The same is
  listed below as "can start now" and "blocked".
- `Plan › Milestones` holds the milestones (start the text with `MM/DD`).
  `Plan › Timeline` shows each one with progress over everything it waits for.
