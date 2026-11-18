# CUBIC

A shared workspace for the Best-shot team, shown as a cube you can turn: six
sides, four spaces per side, 24 places to think and decide together.

Code: https://github.com/Ulziibadrakh-tech/cubic

The same `index.html` runs in two places:

- **Vercel (the team's home).** The page asks for an email and password and keeps
  the team's data in a Supabase database (`supabase/schema.sql`). Accounts are
  made by the team's owner; there are no public sign-ups.
- **Claude** (https://claude.ai/artifact/VXX9JmKG23SPdy8pm1ZMSp), the team's
  first copy, with its data in the artifact's own database. Once the data has
  moved, the owner writes `meta/home` there and that copy turns read-only with
  a link to the new home.

"Data" in the menu (export/import) still moves data between copies by hand.

## What it does today

The screen is in English. On a phone, home is only the top bar and the cube:
the bell ("For you"), the chat, and the person, who opens the menu (timer,
search, all spaces, the team with its world clock, data and backup, theme).

- **Sign-in (Vercel).** Email and password, for accounts the team's owner
  made. Wrong password, too many tries, no connection and a server that does
  not answer are said in plain words. Forgot your password? Ask the owner to reset it (no email is ever
  sent). An account that is not on the team yet is told so. The menu shows who
  is signed in, and has "Change password" (the current one is asked for) and
  "Sign out". Sign out forgets the sign-in in this browser at once (its other
  tabs show the sign-in page too) and ends it on the server; a refresh still on
  its way when you sign out is never kept. If the sign-in runs out while the
  page is open, the sign-in page comes back over it, what you typed stays where
  it was, and once you sign in again you are back where you were.
- **Login.** Then, once, a full-screen page asks "Who is working?": Vida,
  Anand or Obama, with each one's role, city and local time. The account (the
  Vercel sign-in, or the Claude account inside Claude) is the identity; this
  only picks the name.
- **Cube.** Drag the cube freely; it carries on with the speed of the drag and
  settles on the nearest side. A side's words show as it turns toward you, so
  the sides seen at an angle are plain colour. Arrow keys work when it has focus. "All spaces"
  in the menu lists the same 24 spaces without 3D. Links such as `#f0-c2` open a
  space directly.
- **Spaces are to-do lists.** Type a line and press Enter. Tick it when done,
  tap the text to edit, tap the round badge for the owner, swipe the row
  sideways to delete (with undo). The ✕ button does the same without a swipe.
- **People and handing over.** Vida and Anand build the technology (Anand as
  forward deployed engineer, with the restaurants); Obama runs business,
  marketing and branding. A new to-do starts with its side's owner (Pilots →
  Anand, Technology → Vida, Marketing, Business & Finance and Branding →
  Obama). The badge opens the owner sheet: hand your to-do to one of the other
  two, or offer to take a teammate's. The one asked accepts or declines, on the
  row or under "For you"; until then the owner stays. One request at a time:
  while one is open, the sheet says who is waiting, and only the one who made it
  can take it back or ask someone else. Ticking a to-do ends its request. Before
  a tap there (or an Accept or Decline) writes, the page reads the to-do again
  from the team's data, so a change made a moment earlier, here or on a
  teammate's phone, is not written over: nothing is saved and the page says so
  (a typed note stays). While the page checks and saves, the buttons wait and
  the owner sheet says so. The sheet suggests whom to
  ask and says why (whose side it is, the kind of work, whether it is daytime
  there, open to-dos). No answer for 12 hours offers the other person; when
  both decline, the page offers to decide in the chat. "Do next" lists
  each person's to-dos that can start now, the nearest milestone first.
- **Chat and updates.** One team chat, with everyone's local time on top. The
  bell shows requests and what the others changed since you last looked, each
  to-do once; a first visit counts as a look, so what is written while you are
  away is new next time. A message or change from a phone with a wrong date
  ("from tomorrow") counts until you have seen it, not until its date comes, and
  never hides what comes after it. A short note appears when something changes while the
  page is open. A viewer who can only read gets no box to write in, and the
  chat and the menu say why. (Phone alerts while it is closed come later, with
  Telegram.)
- **Older to-dos.** Some were first written in Mongolian and carry an English
  version next to the text (`en`, made from `enSrc`). The page shows that
  English while it still matches the text; editing such a to-do stores the
  English as its text.
- **Time.** A person's timer starts when they open the workspace and stops on
  "Pause". Time is attributed to the space they are in. After more than
  10 minutes away the page asks whether to count that time (with the plan
  graph in full screen, under the graph's own bar, where a timer change that
  could not be saved is said too). The team list
  shows each person's status, open to-dos, today and the last 7 days.

- **Reorganise.** Tap a to-do's text to edit it or move it to another space
  (with undo). Paste several lines into "New to-do" to add one to-do per line;
  list bullets and numbers are stripped.
- **Data.** Export everything (sides, to-dos, people, time) as JSON,
  or the to-dos as CSV (with a `deps` column). Inside Claude it is saved
  through the page's download permission; on a phone the share sheet offers
  Proton Drive. On Vercel the phone's own share sheet is used when it takes the
  file (Proton Drive is offered there too); otherwise the browser downloads it,
  and the page says to look in Downloads and move it to Proton Drive (it never
  claims a download was saved). "Copy" always works. Import takes a JSON export or a
  CSV with at least `cell` and `text` columns, shows what will change, and
  writes only after you confirm. Import never deletes: a to-do with the same
  id is updated only if the imported one was edited later, and only with what
  the file holds, so the rest is kept (a CSV has no English version, no file
  carries an open hand-off request; a new owner or a tick ends one); a CSV without a
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
| `dev/serve.py` | Local server. Serves `index.html` with stand-in data so it runs without Claude, and a stand-in Supabase for the Vercel copy (`?bare=1&remote=1`). |
| `dev/fake-claude.js` | The stand-in for sign-in and shared data inside Claude. Stores data in the browser's localStorage. |
| `supabase/schema.sql` | The team's database on Supabase: one table of documents, members only, writes through three functions. Not deployed. |
| `dev/supabase_load.py` | Copies the Claude copy's data into Supabase (the move). Dry run unless `--write`. |
| `tests/check_flow.py` | Phone-sized browser check of the main flows, both copies (211 checks). |
| `tests/check_loader.py` | Checks of the loader's changes and SQL on made-up data. |
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
- As on Vercel, with no team database (data in this browser only): http://localhost:8800/?bare=1
- As on Vercel, with a stand-in team database on the local server:
  http://localhost:8800/?bare=1&remote=1 . Accounts: `vida@test.invalid` /
  `vida-pass-1`, `anand@test.invalid` / `anand-pass-1`, `obama@test.invalid` /
  `obama-pass-1`, and `guest@test.invalid` / `guest-pass-1` (signs in, but is not
  on the team). It is kept in memory: restarting the server clears it.
- As on Vercel, against the real Supabase project (by hand only, never in the
  checks): http://localhost:8800/?bare=1&remote=live

## Check it

```
pip install playwright
playwright install chromium
py dev/serve.py                 # keep running in one window
py tests/check_flow.py          # in another
py tests/check_loader.py        # the loader, on made-up data (no browser, no database)
```

## Publish a change

Vercel deploys from GitHub on every push to `main`. It's a static site: no
build step, framework preset "Other". `vercel.json` sets a
Content-Security-Policy (Google Fonts and the team's Supabase project besides
the page itself), no framing, and no camera, microphone or location. The local
server applies the same headers to `?bare=1`, and a check fails if the policy
blocks anything the page uses.

The Claude artifact is separate: until it is archived, ask Claude, in a session
with this folder connected, to "publish index.html to the CUBIC artifact".
Claude republishes the file to the same page; the data and the link stay as
they are. See `CLAUDE.md` for the exact identifiers.

## Where the data lives

On Vercel: the Supabase project, table `public.docs`, one row per document
(`path` such as `items/<id>`, `data`, `deleted`, `version`, `updated_at`,
`updated_by`). Inside Claude: the artifact's own database, until that copy is
archived. The documents are the same in both:

| Path | One document per | Fields |
|---|---|---|
| `faces/f0` to `faces/f5` | side of the cube | `title`, `cells` (four names), `editedBy`, `editedAt` |
| `items/<id>` | to-do | `cell` (`f0-c2`), `text`, `url`, `who` (`vida`, `anand`, `obama` or empty), `done`, `deps` (ids it waits for), `by`, `at`, `editedBy`, `editedAt`, `act` (the last kind of change), `ask` / `nope` / `declined` (handing over), `en` / `enSrc` (English of an older Mongolian line) |
| `people/<person id>` | signed-in person | `who` (which team member they are), `at` |
| `clock/<person id>` | signed-in person | `days` (`{date: {ms, bySpace}}`), `openSince`, `openCell`, `leftAt`, `paused` |
| `chat/<id>` | chat message | `by` (account id), `text`, `at` |
| `team/<vida\|anand\|obama>` | team member | `city`, `tz` (for the world clock) |
| `seen/<person id>` | signed-in person | `updates`, `chat` (up to when they last looked: the time of the newest change or message they saw, or where a first visit began); `ahead` (`{updates: ['<time>:<to-do id>'], chat: ['<message id>']}`, at most 300 each: what they saw although it is dated after the mark) |
| `meta/home` | (one) | `url`: where the team works now; `at` (optional): when it moved. The owner writes it after the move; the Claude copy then turns read-only (no writes, no timer, no chat box) and counts a timer left running only up to `at` (else up to when the page saw the note). The page never writes `meta`. |

A side that has never been renamed has no document; the page falls back to the
names in `DEFAULT_FACES`. Times are milliseconds since 1970, UTC. Day keys use
the viewer's local date.

## The team's database (Supabase)

- **Schema.** `supabase/schema.sql` (run it in the SQL editor; it can be run
  again). One table of JSON documents. Only accounts listed in
  `public.members` can read or write; the page writes only through
  `doc_set`, `doc_update` (a merge of the fields given) and `doc_delete`
  (which only marks the row `deleted`: nothing is ever removed, so undo works
  and every open page hears about it). Each write gets a `version` that only
  goes up; pages ask every few seconds for what changed.
- **Adding a person.** Supabase dashboard, Authentication, Users, Add user,
  Create new user, with "auto confirm" on. Then the account's id goes into
  `public.members` (a Claude session with the Supabase token can do it). The
  person signs in and picks their name once.
- **Passwords.** At least 8 characters. Changing one asks for the current one.
  Supabase's own mail does not reach the team, so there is no "forgot password"
  email: the owner sets a new password in the dashboard.
- **Never** turn on public sign-ups or anonymous sign-ins, never hard-delete
  rows, and never put the secret key or the service-role key in the page or in
  this repository. The publishable key in `index.html` is public by design: it
  only lets a browser ask, and the database decides.
- A free Supabase project pauses after a week without use; open it in the
  dashboard to wake it.
- **The move from Claude.** Save the artifact's documents into `data/` (as
  ArtifactData `list` with `out_dir` writes them: `data/<folder>/<collection>/<id>.json`),
  then `python3 dev/supabase_load.py data/<folder>` shows what would change, and
  `--write` writes it. It turns to-dos that carry an English version into that
  English, closes open timers, never deletes, and never overwrites a newer
  document. Run again (a later export), it leaves deleted what the team deleted
  on Vercel since, unless that to-do was changed after the delete. Its output
  names documents by path, space and length only, never their words. Then the
  owner writes `meta/home` `{ "url": "https://…", "at": <ms now> }` into the
  Claude copy.

## Known limits

- On Vercel, accounts are email and password, made by the owner; inside Claude
  the Claude account signs in.
- A person with the page open on two devices at once can get a skewed timer.
- The timer measures "timer running", not proven work.
- Anyone on the team (a member on Vercel, Contributor access inside Claude)
  can edit any entry, including another person's timer document. Fine for
  three people who trust each other.
- Edits to a to-do change only the fields touched (a merge), so two people
  changing different things on the same to-do both keep their change (the
  checks run two phones against the stand-in database). Not yet tried by two
  real people at the same moment.
- On Vercel a change made elsewhere shows within a few seconds (the page asks
  every 3 s while it is in front, every 30 s in the background).

## Plan side: dependencies, graph, timeline

- Edit a to-do and use "Waits for" to say what it waits for.
- `Plan › Dependency graph` sorts every linked to-do topologically (Kahn's
  algorithm). Each column is a layer: a to-do sits one layer to
  the right of the latest thing it waits for. Only direct needs are drawn; a
  need already implied by another is counted under the graph. Tap a node to
  see its chain. "Build order" lists the whole build order, numbered; below
  it are "can start now" and "blocked". A loop of waiting (possible only when
  two people edit at once) is named with a warning, never hidden.
- When the workspace holds more than one separate plan, a row of buttons picks
  which one the graph and the timeline show (the most recently added first),
  with "All" for all.
- `Plan › Milestones` holds the milestones (start the text with `MM/DD`, or
  `MM/DD/YYYY`); each row says its days left or late and how much is done.
  `Plan › Timeline` is the same graph as the dependency graph, grouped by
  milestone in date order: each to-do sits before the first milestone that
  needs it. A ruler over each group gives the milestone's date, its state in a
  word and the days left ("11/20 · Due soon · 1 day left"; a late one says it
  once: "11/14 · 3 days late"); each
  milestone node carries its date, days left and a progress bar, and a thin
  line marked "Today" shows where today falls. The bar above says what is done
  ("Done 2/41 · 5%") and whether the plan is on time. Both graphs look like a
  printed project chart in the page's light or dark theme: neutral cards with
  a status icon and word, one accent for the to-dos holding others up now, and
  red only for what is late, "Behind" and loops. Both zoom and open full
  screen. Nothing stores when a to-do was ticked, so there is no invented
  history.
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
writes a normal CUBIC export. Import it from "Data" › "Import", or ask
Claude to write it into the artifact's database. Each space then lists its
tasks in build order.
