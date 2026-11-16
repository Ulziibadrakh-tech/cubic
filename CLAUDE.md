# Working on CUBIC

CUBIC is one file, `index.html`, published as a Claude artifact. Keep it one
file: no build step, no framework, no dependencies beyond the two Google Fonts.

## The live artifact

- Slug: `e702c5a1-64a8-4f1c-9d1b-7dfb135f1869`
- URL: https://claude.ai/artifact/VXX9JmKG23SPdy8pm1ZMSp
- Declared capabilities: `{"db": {}, "user": {"scopes": ["profile"]}}`

To publish: read the current version with `get_artifact`, then republish
`index.html` with that `base_version`. Capabilities carry forward. Never create
a second artifact for the same workspace: the team's data is tied to this one.

## GitHub and Vercel

- Code lives at https://github.com/Ulziibadrakh-tech/cubic (branch `main`).
- Vercel serves the same `index.html` as a static site. Without
  `window.claude` the page uses `localStore()`: data stays in that browser,
  and the page says so. The artifact remains the team's shared copy.
- Moving data between them is the "Өгөгдөл" export/import. Import only adds
  or updates newer records; it never deletes.

## Working from a phone

The team works mostly from phones, through Claude sessions on this repository.

- Work on a branch and open a pull request; say what changed and how it was
  checked. Run `tests/check_flow.py` in the session before opening the PR and
  paste its last line into the PR; there is no CI yet.
- Merging to `main` redeploys the Vercel copy by itself. The Claude artifact
  (the team's shared data) is separate: republish `index.html` to it after the
  merge, as described above.
- For any visible change, attach phone-sized screenshots to the PR
  (`py tests/check_flow.py - shots` writes them) so it can be judged on a phone.

## Before changing the page

1. Run `py dev/serve.py` and `py tests/check_flow.py`. All checks must pass
   before and after. `?bare=1` serves the page as Vercel does.
2. `dev/serve.py` finds the main script by the marker `<script>\n'use strict';`.
   Keep that marker.

## Rules the page must keep

- **Data is the team's.** Never hardcode entries in the page. Never delete or
  rewrite `items` or `clock` documents unless the user asks for that exact
  change. Read with `db_list` before any `db_write`, and pass `ifVersion`.
- **No silent loss.** Every write goes through `save()` or `writeClock()`. A
  failed save keeps the person's text on screen and says so.
- **Subscribe once.** `onSnapshot` is called once per collection in
  `connect()`. Never from a render function.
- **No writes on a tick.** The timer writes only on a change of state (start,
  pause, change of space, leaving, returning). Running totals are computed on
  screen from `openSince`.
- **Do not redraw under an open edit.** Use `queueRender()`, not `renderAll()`,
  from snapshot callbacks.
- **Store ids, not names.** A signed-in person maps to a team member through
  `people/<id>.who`; names come from `TEAM` at render time.
- **Works without the cube.** Every space stays reachable from "All spaces",
  by keyboard, and with reduced motion.
- **Both themes.** Colors come from the tokens in `:root`; the dark values are
  defined twice (media query and `[data-theme="dark"]`).
- **Plain words on screen.** No jargon, and never a status the page cannot
  back up.

## What the artifact sandbox does not allow

`alert`/`confirm`/`prompt`, downloads, service workers, `fetch` or WebSocket to
other hosts, remote images. Only a plain `#name` survives in the URL: no query
strings, so routing uses `#f2` and `#f2-c1`.

## Product stance

- Three skilled people: Vida, Anand, Obama (`TEAM` in `index.html`).
- A space is a plain to-do list: short lines, tick, owner, swipe to delete.
  No due dates, priorities or required fields.
- Sides: Pilots, Plan, Technology, Marketing, Business & Finance, Branding
  (`DEFAULT_FACES`). Side and space names are English; to-do text is Mongolian.
- A to-do may wait for others: `deps` is a list of item ids. Status is always
  derived by `flow()` (done / blocked / blocking / open), never stored.
  `wouldLoop()` must guard every place that adds a dependency.
- Two Plan spaces are views, not lists (`isViewCell`): `f1-c0` Timeline and
  `f1-c3` Dependency graph. Milestones are the to-dos in `f1-c1`; a leading
  `MM/DD` in the text is the date the Timeline sorts by.
- Status is written in words and a mark, never by colour alone.
- Screen text and seeded content are Mongolian. Keep lines short.
- The cube should feel physical: free drag, momentum, a soft settle. Do not
  replace it with fixed step animations.
- Time is shown per person and per space, never as a ranking.
- Integrations (Telegram, Proton, Claude) come later; see `docs/ROADMAP.md`.
