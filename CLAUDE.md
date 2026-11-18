# Working on CUBIC

CUBIC is one file, `index.html`. Vercel serves it as the team's home, with the
team's data in Supabase; it is also the Claude artifact below, the first copy,
until that is archived. Keep it one file: no build step, no framework, no
dependencies beyond the two Google Fonts (no supabase-js either).

## The Claude artifact (the first copy)

- Slug: `e702c5a1-64a8-4f1c-9d1b-7dfb135f1869`
- URL: https://claude.ai/artifact/VXX9JmKG23SPdy8pm1ZMSp
- Declared capabilities: `{"db": {}, "user": {"scopes": ["profile"]}, "downloads": true}`
  (`downloads` lets "Save JSON" save a backup file; on a phone the share sheet
  offers Proton Drive. Pass the whole set when publishing. Version 8 still
  declares `sample`, from the Translate button that is gone; the next publish
  drops it.)
- Collections: `faces`, `items`, `people`, `clock`, `chat` (messages:
  `{by: account id, text, at}`), `team` (`team/<vida|anand|obama>`: `{city, tz}`
  for the world clock), `seen` (`seen/<account id>`: `{updates, chat, ahead}`,
  up to when that person has looked. A look's mark is the newest change or
  message on screen that is not dated after now; a first visit's mark, and one
  that replaces a mark this phone cannot trust (more than 2 minutes ahead of
  its now), is where that visit began, by that phone's clock. `ahead` lists
  what was seen although dated after the mark: on screen from a phone with a
  wrong date, or covered by the mark it replaced), `meta` (`meta/home`: `{url, at?}`, written by
  the owner once the team has moved; the Claude copy then turns read-only and
  links there. The page never writes `meta`).
- Archive rules (Claude copy only): nothing is written and nobody is asked who
  they are until meta's first answer from the server (`metaKnown()`). Once
  `meta/home` is there, `save()` refuses and keeps the text, the chat box gives
  way to the link, the page's running timer is closed with one last write, and
  a timer left open counts only up to `at` (`endOf()`). A cached answer may
  turn the copy read-only, never back. Redraw with `queueRender()`.

To publish: read the current version with `get_artifact`, then republish
`index.html` with that `base_version`. Capabilities carry forward. Never create
a second artifact for the same workspace: the team's data is tied to this one.

## The team's database (Supabase)

- Project `ubecsmzurrloglegwfsf` (https://ubecsmzurrloglegwfsf.supabase.co).
  The page holds its URL and publishable key (`REMOTE_DEFAULT`): public by
  design, it only lets a browser ask. Never put the secret or service-role
  key in the page or the repository.
- Schema: `supabase/schema.sql` (not deployed). One table, `public.docs`, with
  the same paths as the artifact. Members only (`public.members`). Writes only
  through `doc_set`, `doc_update` (top-level merge) and `doc_delete` (marks
  `deleted`, keeps the data); each write bumps `version`.
- Never turn on public sign-ups or anonymous sign-ins. Never hard-delete rows:
  open pages learn of a delete only through the `deleted` mark.
- Adding a person: dashboard → Authentication → Users → Add user → Create new
  user, auto-confirm on; then the account's id goes into `public.members` (a
  Claude session with the Supabase token can do it). No email is ever sent:
  the owner resets a forgotten password in the dashboard.
- A free project pauses after a week without use; wake it in the dashboard.
- The move: `dev/supabase_load.py DIR` (dry run; `--write` writes) loads an
  ArtifactData export (`DIR/<collection>/<id>.json`, kept in `data/`): English
  versions become the text, open timers are closed, newer documents win,
  nothing is deleted, a row deleted on Vercel stays deleted unless the
  document changed after the delete, `seen` stays behind. Its output never
  shows a document's words. Never run it with `--write` from a check. Then
  the owner writes `meta/home` into the artifact.

## GitHub and Vercel

- Code lives at https://github.com/Ulziibadrakh-tech/cubic (branch `main`).
- Vercel serves the same `index.html` as a static site: the sign-in page, then
  the team's database through `remoteStore()`. `window.CUBIC_REMOTE = null`
  (dev only) means no team database: `localStore()`, data in that browser,
  and the page says so.
- Moving data by hand is the "Data" export/import. Import only adds or
  updates newer records; it never deletes.

## Working from a phone

The team works mostly from phones, through Claude sessions on this repository.

- Work on a branch and open a pull request; say what changed and how it was
  checked. Run `tests/check_flow.py` (and `tests/check_loader.py`) in the
  session before opening the PR and paste the last line into the PR. CI
  (`.github/workflows/check.yml`) runs the same checks on every push.
- Merging to `main` redeploys the Vercel copy by itself. The Claude artifact
  is separate: until it is archived, republish `index.html` to it after the
  merge, as described above.
- For any visible change, attach phone-sized screenshots to the PR
  (`py tests/check_flow.py - shots` writes them) so it can be judged on a phone.

## Before changing the page

1. Run `py dev/serve.py` and `py tests/check_flow.py`. All checks must pass
   before and after. `?bare=1` serves the page as Vercel does, with no team
   database; `?bare=1&remote=1` adds the stand-in Supabase in `dev/serve.py`
   (accounts there, test hooks under `/sb/__test/`); `?bare=1&remote=live` is
   the real project, for a person checking by hand only, never the checks.
2. `dev/serve.py` finds the main script by the marker `<script>\n'use strict';`.
   Keep that marker.
3. The stand-in answers exactly like Supabase (status codes, JSON shapes,
   versions). When the page starts using another endpoint or field, add it
   there the same way.

## Rules the page must keep

- **Plans are private.** This repository and the Vercel site are public. A
  team's real plan goes into the artifact's database (or `data/`, ignored by
  git and Vercel), never into a tracked file, a screenshot or a PR text.
- **Data is the team's.** Never hardcode entries in the page. Never delete or
  rewrite `items` or `clock` documents unless the user asks for that exact
  change. Read with `db_list` before any `db_write`, and pass `ifVersion`. On
  Supabase, write only through the three functions (or the loader), and read
  the rows you are about to change first.
- **One shape, three stores.** `remoteStore()` (Vercel) and `localStore()`
  have the shape of Claude's db and user; nothing above `connect()` knows
  which one it runs on (only the account controls look at `S.remote`).
- **The Vercel store.** One full read after sign-in, then polls for rows
  changed since the newest one seen, with 15 s of overlap. Polls page by key
  (`updated_at`, then `path`), never by offset, and only rows from a poll move
  the window on (a row from the page's own write does not). A row is taken
  only when its `version` is newer. Sign out forgets the sign-in in this
  browser first, then ends it on the server under the `cubic-refresh` lock; a
  refresh answer that lands after that is never kept.
- **No silent loss.** Every write goes through `save()` or `writeClock()`, and
  each one through `dbWrite()` (one write at a time per document, one retry on
  `unavailable`). The one exception is `markSeen()`: a `seen/<id>` mark is not
  the person's work, so it uses `dbWrite()` alone and a lost one only brings a
  count back. It is the only place that writes `seen/` (a first visit's mark
  too, through `seenStart()`). A failed save keeps the person's text on screen
  and says so (`sayFailed()`): in the status line, and also at the top of an
  open sheet or the login, or under the full-screen graph's bar, which cover it.
  Changing a to-do is `patchItem()`, a merge of the changed fields, never a
  rewrite of the cached document. No write before the data has loaded
  (`canWriteNow()`), and no clock write before that person's clock has loaded.
- **Subscribe once.** `onSnapshot` is called once per collection in
  `connect()` (meta, faces, items, people, clock, chat, team, seen). Never
  from a render function.
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
  defined twice (media query and `[data-theme="dark"]`). `--accent` is for text
  and outlines, `--accent-fill` for filled buttons (white text must pass 4.5:1).
  The theme button stores a per-viewer choice; with none, the phone decides.
  The plan graph follows the theme too, through its own `--g-*` tokens, whose
  dark values are also defined twice (media query and `[data-theme="dark"]`).
- **English only.** Every string on screen goes through `tr(key, ...)` with an
  entry in `L.en`; static markup uses `data-t`, `data-t-ph`, `data-t-al`,
  `data-t-ti`. Never hard-code screen text. Side and space names are the team's
  own words. Older to-dos were written in Mongolian and may carry an English
  version (`en`, made from `enSrc`): the page shows it while `enSrc` still
  equals `text` (`txt(i)`, `hasEn(i)`). Saving an edit of such a to-do writes
  the English as its `text` and clears `en`/`enSrc`. The checks fail on any
  Mongolian letter in the page source (apart from the pattern that reads an old
  `М1` milestone tag) or on screen.
- **Plain words on screen.** No jargon, and never a status the page cannot
  back up.

## What the artifact sandbox does not allow

`alert`/`confirm`/`prompt`, direct downloads (only `downloads.save`), service workers, `fetch` or WebSocket to
other hosts, remote images. Only a plain `#name` survives in the URL: no query
strings, so routing uses `#f2` and `#f2-c1`.

## The phone screen

- Home is the top bar (wordmark, bell, chat, the person) and the cube.
  Nothing else: the timer, search, "All spaces", the team with its world
  clock, data and backup, and the theme live in the menu (the person button).
- On Vercel the sign-in page (`signInPage`: email and password, accounts made
  by the owner) comes first; the 10-second "slow" note counts only from
  sign-in on. A sign-in that runs out brings it back over the page, and
  nothing under it is redrawn away. "Change password" and "Sign out" are in
  the menu's person card (Vercel only).
- The login page (`showLogin`) asks which of the three this account is, once.
  It is not a password: the account (the Vercel sign-in, the Claude account,
  or this browser) is the identity. "Switch person" in the menu brings it back.
- Sheets (`openSheet`/`closeSheets`) make the page under them inert; Escape
  closes them. Only one is open at a time. Where the page owns its history
  (Vercel, top level), an open sheet holds one history step, so the phone's
  back gesture closes it and the next one goes where Back would have gone
  without the sheet (from a space, the cube; from home, out of the site);
  closing it any other way takes the step back off. Inside Claude's frame no
  step is added. A space opened from a sheet opens only after that step is
  off (`sheetNext`).
- A toast belongs to the page: under a sheet or the login it is inert with
  the page (`toastUnder()`), so a hidden Undo is never reached with Tab. Only
  an answer given in For you, said while For you is still open, shows over
  it (`toast(text, null, raise)`); a tap on a toast without Undo puts it
  away and reaches nothing under it. A toast with Undo is never raised.

## Product stance

- Three skilled people (`TEAM` in `index.html`): Vida and Anand build the
  technology, Anand as forward deployed engineer with the restaurants; Obama
  runs business, marketing and branding. A new to-do starts with its side's
  owner (`SIDE_OWNER`: Pilots → Anand, Technology → Vida, Marketing, Business &
  Finance and Branding → Obama, Plan → nobody).
- Handing over (the owner badge opens the owner sheet): the owner, or anyone
  for a to-do with no owner, asks one of the other two to take it; someone who
  wants a teammate's to-do offers to take it. The one asked accepts or
  declines; until then the owner stays. `ask` holds the open request, `nope`
  who declined since the owner last changed, `declined` the last no. Only the
  person asked answers (`answerHand`); accept writes `who`. One request at a
  time: while one is open, only the one who made it can take it back or ask
  someone else (`mayAsk`). A new owner or a tick ends a request (`tickOf`,
  also on import). A hand-off tap acts on what was drawn: it checks the to-do
  as this page has it, then as the store has it now (`freshItem()`, a server
  read on Vercel), and writes nothing if it changed before that read
  (`doc_update` has no version check, so a change in the moment between the
  read and the write can still be written over). One tap at a time per
  to-do (`handBusy`): its buttons wait, and the owner sheet says so.
- Deciding fast: the sheet suggests whom to ask and writes why (`rankFor`:
  the side's person, the kind of work, awake now by their local time, open
  to-dos; whoever declined goes last). No answer for 12 hours offers the other
  person; both declined sends the decision to the chat. "Do next" (`doNext`)
  ranks a person's to-dos that can start now: nearest milestone, then most
  unblocked, then build order. All of this is worked out on screen; nothing
  is decided for people.
- A space is a plain to-do list: short lines, tick, owner, swipe to delete.
  No due dates, priorities or required fields.
- Sides: Pilots, Plan, Technology, Marketing, Business & Finance, Branding
  (`DEFAULT_FACES`). Side names, space names and to-do text are English.
- A to-do may wait for others: `deps` is a list of item ids. Status is always
  derived by `flow()` (done / blocked / blocking / open), never stored.
  `wouldLoop()` must guard every place that adds a dependency (the editor at
  save time, import through `dropImportLoops()`). `plan()` is the topological
  sort (Kahn): build order, layers, loops, separate plans, implied links.
- Two Plan spaces are views, not lists (`isViewCell`): `f1-c0` Timeline and
  `f1-c3` Dependency graph, both drawn by `drawFlow()` as one graph. The
  Dependency graph has a column per layer; the Timeline has a group of
  columns per milestone in date order, with today drawn between the groups.
  Milestones are the to-dos in `f1-c1`; a leading `MM/DD` in the text is the
  date the Timeline sorts by. Milestone nodes and Milestones rows carry the
  date state and progress (`milestoneData`).
- Status is written in words and a mark, never by colour alone.
- Screen text and seeded content are English. Keep lines short.
- The graph reads like a printed project chart and follows the light and dark
  theme through the `--g-*` tokens (dark values defined twice: media query and
  `[data-theme="dark"]`). One accent marks the to-dos holding others up now and
  the links out of them; red is only for what is late, "Behind" and loops (a
  loop is the one dashed card). Status is always a mark and a word: graph cards
  use the SVG icons (`statusIcon()`, `aria-hidden`) beside the status word.
  Hover styles sit in `@media (hover: hover)`; reduced motion turns the graph's
  transitions off. It zooms and opens full screen; `syncFull()` works out the
  lock and the inert page under it from the state after every draw and route,
  and moves a failed save and the away question under the graph's bar while
  full screen is on. Its bar says what is done
  ("Done 2/41 · 5%") and whether the plan is on time (due by today = what the
  milestones dated today or earlier need). Nothing stores when a to-do was
  ticked, so it never invents a history.
- The cube should feel physical: free drag, momentum, a soft settle. Do not
  replace it with fixed step animations. A side's words and tiles show only
  as it faces you (`--see` per face, from `facing()` in `applyCube()`), so the
  sides seen at a steep angle are clean colour; words drawn nearly edge-on
  look broken on a phone. Face corners stay rounded only a little, so the
  edges meet.
- Time is shown per person and per space, never as a ranking.
- Chat is one shared conversation. Updates are worked out from the to-dos
  (who changed one last, when, and `act`: add, done, undone, owner, move,
  edit, ask, accept, decline, cancel); the bell counts what is new since
  `seen/<id>` plus requests waiting for this person (a request counts once).
  A first visit counts as a look. A viewer who cannot write gets no box to
  write in; the chat and the menu say why. Alerts show while the
  page is open; phone alerts while it is closed come with Telegram.
- Integrations (Telegram, Proton) come later; see `docs/ROADMAP.md`.
