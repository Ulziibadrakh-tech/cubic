# CUBIC roadmap

`docs/SPEC.md` is the long-term reference. This file records what exists and
the order the rest would come in. Nothing below is committed to a date.

## Built

| Spec area | State |
|---|---|
| Cube, six sides, four spaces each, rename in place | Done |
| Pointer, touch and keyboard rotation; reduced motion | Done |
| Non-3D route to all 24 spaces | Done ("All spaces") |
| Deep links to a side or a space | Done (`#f2`, `#f2-c1`) |
| Inline capture and editing with saving / saved / failed states | Done |
| To-do lists per space: tick, owner, swipe to delete, undo | Done |
| Cube motion with momentum and a soft settle | Done |
| Individual identity | Done: Claude sign-in, then pick Vida, Anand or Obama |
| Actual active time per person and per space | Done, started on open, with an away check |
| Attribution (who, when) on entries | Done |
| Search and recent changes | Done, simple |
| Export / import (JSON, CSV), move a to-do, paste many lines | Done |
| Swiss visual style, both themes | Done |
| Vercel copy (browser-only data) | Done; shared data stays in the artifact |
| Dependencies between to-dos, derived blocked / blocking status | Done |
| Dependency graph and milestone timeline (Plan side) | Done |
| Backup file from inside Claude (save to Proton Drive by hand) | Done, via the `downloads` capability |

## Decided against, for a team of three

- Roles and per-record permissions. Sharing level on the page is the only gate.
- Due dates, priorities and checklists. To-dos have text, owner, done and what
  they wait for. A milestone's date is the `MM/DD` at the start of its text.
- Completion percentages for sides or the cube.

Revisit if the team grows or outside people are invited.

## Next, in the order they would help

1. **Experiment records.** A structured entry for the Pilots side: hypothesis,
   change, restaurant, number to watch, result, decision.
2. **Activity history.** Keep earlier versions of an entry so an edit can be
   reviewed, not only the last editor.
3. **Time corrections.** Let a person fix a day's total, with a reason kept.
4. **Notifications.** In-page first.

## Later

- Telegram alerts, Proton (only a verified, officially supported route),
  Claude summaries with source links and human confirmation.
- A hosted app with its own sign-in, if CUBIC must work for people without a
  Claude account. That is a rebuild of the data layer, not an addition.
- 3×3×3. The spec asks to confirm what that means before building it.

