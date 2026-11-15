CUBIC — Claude Code Master Prompt

You are Claude Code acting as a senior product engineer, product designer, and software architect. Build **CUBIC**, a visual operating system for a startup team. Follow this specification, but first inspect the existing repository and adapt to its actual state. Do not assume a framework, database, hosting provider, authentication system, or existing product details that you have not verified.

## 0. Repository-first instructions

Before changing files:

1. Inspect the repository tree, README, package/build manifests, existing application routes, design system, tests, environment/configuration examples, and relevant project instructions such as `AGENTS.md`.
2. Identify the current stack and how the application is run. Preserve working conventions and reuse existing components and services where sensible.
3. Summarize the current state and propose a short implementation sequence. If this is an empty repository, say so and recommend a minimal stack with reasons before building. If there are multiple plausible application roots or a destructive migration is needed, ask one focused question before proceeding.
4. Do not overwrite existing work, expose secrets, or commit credentials. Never invent repository details in documentation or the implementation.
5. Implement a coherent, runnable product slice. Keep decisions consistent with this spec and document any necessary deviation and its reason.

## 1. Product vision

**CUBIC is a flexible, shared workspace where a startup team can think, plan, discuss, and do its work together.** Its primary interface is a tactile, rotatable 2×2×2 cube. Six faces represent configurable work domains; four cells on each face represent subspaces, for 24 starting work areas. The cube helps people find and see their work; it must not force every idea into a rigid process.

The product should help a team think through questions like: What are we exploring? What should we do next? Who is involved? What changed? What is getting in the way? Time information should add useful context, not pressure people into a fixed way of working. A completed cube means the team considers its chosen work areas complete for now; it must not imply that all future company work has ended.

## 2. Assumptions (make configurable; do not present as known facts)

- Initial workspace has three teams, each with a team space and separate team-level access boundaries. A person may belong to more than one team through an individual identity.
- All three teams can see shared work by default, subject to workspace visibility rules; sensitive finance or personnel content can be restricted.
- Six starter faces: Marketing, Milestones, Roadmap, Research, Business & Finance, and Technical. Names, order, colors, subspaces, and ownership are editable by authorized users.
- Four starter subspaces per face are suggestions, not required taxonomy. Teams can rename, rearrange, repurpose, add, archive, leave empty, or ignore spaces as their needs change. Every space can hold any mix of ideas, plans, brainstorms, tasks, notes, and links. Owners, statuses, dates, estimates, and dependencies are optional tools, not required fields.
- Time is recorded as estimates, explicit work sessions, state durations, and user-attributed delay/loss reasons. Do not claim passive tracking or precise causal attribution unless an implemented integration provides it and the user has enabled it.
- Build for one workspace initially, but include tenant/workspace identifiers in the data model to permit future separation and scale.
- Design the domain model so 3×3×3 can be evaluated later. Do not block the MVP on implementing it.

## 3. Core information architecture

### Cube and work units

- A cube has six faces, each with a stable ID, editable title, accessible color token, icon, owner/team, visibility, order, and completion policy.
- Each face starts with four suggested subspaces. They are flexible work areas, not mandatory containers. Teams can rename, rearrange, repurpose, add, archive, or leave them unused. A work area can hold any mix of brainstorms, plans, notes, tasks, discussions, and links. Owners, status, priority, dates, estimates, time entries, blockers, and dependencies are optional and should be easy to skip.
- Do not encode meaning solely through color. Always show labels, icons or patterns, status text, keyboard focus, and accessible contrast.
- Support a 3D cube on pointer, touch, and keyboard. Users can rotate by dragging/swiping, use clear directional controls, and select a face. Rotation settles with reduced-motion support. Provide a 2D accessible face-grid/list alternative for assistive technology, small screens, or reduced motion.
- Selecting a face transitions into its four-cell workspace with a short, purposeful motion. Selecting a cell opens its work area. Deep links must restore the selected face and work unit.
- Show face-level activity and progress when useful, with a clear explanation of what is included. Completion tracking is optional and team-defined. Do not require users to mark every area complete, and do not average incompatible measures into a misleading single percentage.

### Work area

Each work unit offers structured, linked records for:

- Tasks and checklists, status, priority, assignees, due dates, estimates, actual sessions, and blockers.
- Documentation with title, body, author, timestamps, version/history or audit trail, tags, and linked work.
- Discussions/comments with mentions, timestamps, and links to decisions or tasks.
- Decisions with context, options considered, decision, owner, date, and follow-up/revisit date.
- Files/links with metadata and access checks. Use repository-appropriate secure storage or a clearly marked local/demo adapter; do not silently store uploaded content in public paths.
- Dependencies between work units/tasks, with direction, type, owner, due date, current state, and cycle validation.
- Activity history that makes status, ownership, estimate, date, and completion changes attributable and reviewable.

Support search, helpful filters, sorting, saved views if the existing stack permits, and a global activity stream. Do not require formal records for every thought. Let rough ideas become tasks, decisions, or milestones when the team is ready, without imposing a prescribed order. Shared visibility does not mean bypassing access control.

### Inline editing throughout

- Make content editable where it appears: titles, descriptions, labels, notes, brainstorm items, task fields, dates, owners, statuses, decisions, milestones, and face/subspace names.
- Let users activate a field by clicking or keyboard, edit in place, then save or cancel clearly. Keep ordinary edits out of separate settings screens.
- Support quick inline capture and rearrangement of ideas. Rich notes should have an accessible editor with keyboard controls.
- Show whether edits are saving, saved, or failed. If using autosave, preserve drafts through temporary failures and never silently discard changes.
- Respect permissions: show read-only content with a brief explanation where users cannot edit it.

## 4. Identity, teams, and permissions

Implement individual user identities and exactly three seeded/demo teams for initial setup; do not hard-code a permanent maximum of three teams. Login is per person, not a shared credential per team. Each user may have memberships in one or more teams and a workspace role. Provide invitation/onboarding flows appropriate to the existing authentication stack.

Use least-privilege role-based access control, enforced server-side for every read and write. Suggested roles:

- **Workspace owner:** workspace settings, membership, policies, integrations, and all content.
- **Workspace admin:** day-to-day configuration and membership management, without owner-only transfer/destructive controls.
- **Team lead:** manage their team’s membership/assignments and team-visible configuration.
- **Contributor:** create and update work they can access; participate in discussions and decisions.
- **Viewer:** read accessible content and participate only where explicitly allowed.

Model permissions at workspace, team, work-unit, and record levels where required. Include private/restricted records, guest access policy, invitation expiry/revocation, session security, account recovery, and audit events. Never rely on hiding UI controls as authorization.

## 5. Timeline, history, and time intelligence

Provide a clear timeline view (face, work-unit, and workspace scopes) containing planned dates, milestones, dependencies, status transitions, actual sessions, blocked periods, and completion events. Include timeline and list/calendar-friendly alternatives if practical. Preserve history when estimates or dates change; show the previous value, new value, actor, timestamp, and optional reason.

Use these distinct measures and label them exactly:

- **Planned time:** an estimate or forecast, with author, creation time, and revision history. It is not a promise or a measured duration.
- **Actual active time:** duration of an explicitly started work session, excluding paused time. A session can be entered manually with actor and reason; prevent accidental overlapping sessions for the same user/workspace or clearly resolve them.
- **Blocked time:** elapsed time a work item remains in a blocked state, derived from status history. This is elapsed calendar time and may overlap active time by different contributors; never add it to active time as if mutually exclusive.
- **Lost time:** a user-attributed, explicitly logged duration or categorized incident (for example, rework, waiting, interruption, scope change), linked to affected work and optionally a reason. It is a reported/estimated measure, not an objectively inferred fact. Prevent double-counting against sessions where feasible and disclose overlap rules.
- **Elapsed lead/cycle time:** derived calendar duration between clearly named start/end events. State whether weekends/non-working days count.

Never silently infer “wasted” time, rank people by hours, or equate more logged hours with better performance. Dashboards should emphasize flow, risk, estimate changes, blocked causes, and team-level trends, with contextual definitions and data-completeness cues. Permit corrections with audit history.

## 6. Alerts, dependencies, and completion

Create an in-product notification center with configurable events: assignment/mention, due date approaching or overdue, blocked item, dependency at risk/broken, milestone change, decision request, and important status change. Route alerts to responsible users and optionally relevant team members; avoid noisy duplicate notifications. Provide severity, source link, timestamp, read/snooze controls, preferences, and delivery log. Email/Telegram delivery is an optional later/configurable channel, not required for core use.

Dependency changes must be validated and explain downstream risk. Show upstream blockers on the cube/work-unit and timeline; notify owners on meaningful state changes. Support optional due-date rules and escalation policy configured by workspace admins.

Offer completion tracking at task, work-unit, face, and cube level, but keep it optional and configurable. Teams decide which areas, if any, they want to mark as required. Show remaining optional or open work honestly. Reopening and rearranging work are supported. Marking a cube complete must not block further work. Avoid gamification that rewards premature completion or inflated time reporting.

## 7. Dashboards

Provide a concise workspace dashboard with:

- Cube overview with six faces and the four suggested starting subspaces per face; make clear that spaces can be changed or left unused.
- Current priorities, overdue items, blocked work, upcoming milestones, and dependency risks.
- Planned versus actual active time at meaningful aggregate levels, with estimate revision context.
- Blocked and explicitly reported lost time, separately visualized and filterable by period/team/category.
- Recent decisions, changes, and activity.
- Clear ownership and data freshness/completeness indicators.

Users can filter by team, face, owner, status, and period, subject to authorization. Avoid employee surveillance framing and misleading totals.

## 8. Integrations: Telegram, Proton, Claude

Treat integrations as staged adapters behind explicit settings and least-privilege scopes. Core product must work if all providers are disconnected.

- **Telegram:** optional bot integration for configured alerts and deep links. Do not ingest group history by default. Require explicit workspace admin setup, verify webhook/signature or provider mechanism, prevent replay, store tokens as secrets, and allow disconnect/revoke. Keep messages minimal and avoid sensitive content in notifications.
- **Proton:** “Proton” is ambiguous and may refer to Mail, Calendar, Drive, or the user’s broader Proton account. Do not claim an API or capability without verifying current official provider docs during implementation. Design a provider adapter and leave unsupported sync as a clearly documented future connector. No credential scraping, IMAP assumptions, or storing Proton passwords. If mail/calendar support is chosen, use an officially supported authorization mechanism and narrowly scoped access.
- **Claude:** use Anthropic’s supported API only when configured, server-side, with secrets outside the client. Optional AI assistance may summarize selected accessible content, extract candidate action items, or draft a report. Show source links, label generated content, require human confirmation before changing tasks/decisions, respect permissions before sending context, and provide opt-out. Do not make Claude required for normal operation or imply it remembers workspace data automatically.
- Use official, current primary documentation to verify provider/API capabilities before implementing connectors. If browsing/docs are unavailable, implement interfaces and safe placeholders rather than inventing endpoints.

## 9. Data model (adapt to repository conventions)

Create a normalized, tenant-aware schema (or equivalent domain model) with stable IDs, created/updated timestamps, actor references, and appropriate indexes/foreign keys. At minimum represent:

`Workspace`, `User`, `Team`, `Membership`, `Invitation`, `Role/Permission`, `CubeConfiguration`, `Face/Domain`, `WorkUnit/Subspace`, `Task`, `ChecklistItem`, `Document`, `DiscussionThread`, `Comment`, `Decision`, `FileAsset/ExternalLink`, `Milestone`, `Dependency`, `EstimateRevision`, `WorkSession`, `StatusEvent`, `LostTimeEntry`, `Notification`, `IntegrationConnection`, and `AuditEvent`.

Use enums or constrained values for status, priority, visibility, and event type. Store times in UTC and render in user/workspace timezone. Store durations with units and semantics; do not persist ambiguous “time” fields. Keep provider tokens encrypted or in a managed secret store and never in client-readable records. Add migrations and realistic seed data only if consistent with the existing project. Seed demo credentials must be clearly development-only and documented; never enable default production passwords.

## 10. UX and visual principles

- Make the cube memorable and tactile, but keep labels, actions, and data legible and fast.
- Use a polished, contemporary workspace aesthetic: dimensional cube rendering, considered depth/lighting, crisp typography, restrained motion, and distinct domain colors. Avoid visual noise, excessive gradients, and toy-like effects.
- Motion should explain spatial navigation or state change, remain brief, be interruptible, and honor `prefers-reduced-motion`.
- Make core tasks reachable without manipulating 3D. Full keyboard operation, visible focus, semantic controls, screen-reader names, contrast, responsive layouts, and WCAG 2.2 AA as a target.
- Clearly distinguish empty, loading, offline/error, unauthorized, stale, and completed states. Use confirmations for destructive actions and undo where practical.
- Keep terminology consistent: domain/face, work unit/subspace, planned time, actual active time, blocked time, and reported lost time.
- Avoid turning time tracking into an obligation to justify every minute. Make manual entry transparent, contextual, and respectful.

## 11. Technical architecture and quality

Inspect the repository and use its established architecture. Prefer a maintainable modular monolith for the initial product unless the repository already has a justified alternative. Separate presentation, domain rules, persistence, authorization, notifications, and provider adapters. Enforce business rules and permissions on the server/API boundary. Use transactional writes for linked operations and validate input at trust boundaries.

Account for loading and failure states, pagination for growing activity/content, idempotent notification processing, rate limiting for auth and webhooks, secure headers/CSRF protections as appropriate to the stack, output encoding, input validation, safe file handling, and structured logs without secrets or sensitive message bodies. Add observability hooks appropriate to the existing environment. Provide `.env.example` with names and descriptions only, never real values.

Do not add infrastructure or dependencies without need. Do not run tests unless explicitly asked; instead, report what was and was not verified. If the user later asks for verification, run the repository’s appropriate checks and report exact outcomes.

## 12. MVP scope and phased roadmap

### MVP — usable shared workspace

1. Repository-aware setup, authentication, workspace, individual profiles, three configurable teams, memberships, and RBAC.
2. Configurable six-face/four-subspace data structure and responsive interactive 3D cube with accessible 2D fallback.
3. Work-unit detail with tasks, documentation, comments, decisions, links/files, owners, status, and activity history.
4. Milestones, dependencies, timeline, planned estimates, explicit work sessions, blocked-state history, and reported lost-time entries using the definitions above.
5. Dashboard, filters/search, in-product notifications, and completion criteria.
6. Secure local/demo setup and documentation. Provider integrations can be disabled and are not required for acceptance.

### Phase 2 — connected collaboration

Telegram alert adapter; investigate Proton’s currently supported integration surface and implement only a verified supported workflow; Claude assistance with consent, source references, and confirmation; richer exports/reporting; notification preferences; admin audit views.

### Phase 3 — scale and adaptability

Performance and offline resilience, richer analytics, additional teams/workspaces, configurable templates, granular retention policies, API/webhooks, and evaluation of 3×3×3. For 3×3×3, define a migration/configuration model (27 cells across six faces, nine per face) and validate the visual metaphor with users before changing the 24-unit MVP. Do not assume the requested future “3×3×3” means 27 total work units versus another cube convention; present the proposed interpretation for product confirmation before shipping that expansion.

## 13. Acceptance criteria

The implementation is acceptable when, in the context of the inspected repository:

1. A new user can enter a workspace through its supported auth/onboarding flow and see three teams with individual identities and role-aware access.
2. Authorized users can configure six labeled/color-coded domains and four subspaces per domain; all 24 work units can be opened through the cube and through an accessible non-3D route.
3. Cube rotation, face selection, transitions, deep links, keyboard controls, responsive behavior, and reduced-motion preference work without making essential information inaccessible.
4. Users with permission can create/update tasks, docs, comments, decisions, milestones, dependencies, links/files, and owners; relevant changes appear in attributable history.
5. Timeline and detail views distinguish planned, actual active, blocked, reported lost, and elapsed time according to the definitions above. Estimate revisions and corrections remain visible.
6. Blockers, dependency risk, due dates, assignments, and mentions produce sensible in-product alerts with working links and preferences.
7. Dashboard progress and cube completion follow explicit required-work criteria and never conceal optional/incomplete work or combine unlike time measures.
8. Server-side authorization protects every data path; team/user boundaries and restricted records are enforced, with audit events for sensitive changes.
9. Core use works with Telegram, Proton, and Claude disconnected. Integrations do not leak credentials or claim unsupported provider behavior.
10. Completion tracking and space requirements are optional and configurable; users can capture and rearrange ideas without following a fixed workflow.
11. Relevant content can be edited inline, with accessible controls, clear save/error states, and no silent loss of edits.
12. Setup and architecture decisions are documented based on actual repository evidence; the app remains runnable through the project’s existing workflow.

## 14. How to work

After repository inspection, implement incrementally in the existing conventions. First build the information model and navigation shell, then the cube and flexible accessible work areas, then lightweight inline capture/editing and collaboration, then optional time/timeline/alerts, then polish. Keep the workspace adaptable: avoid compulsory statuses, fields, or workflows unless needed for a feature the team explicitly enables. Avoid placeholder controls that appear functional but are not. Where external credentials or verified provider access are absent, provide a clear disabled/configuration state.

Before finishing, summarize: what you inspected, the product slice implemented, important assumptions and deviations, how to run it, configuration needed, and what remains. Be honest about verification; do not claim tests, builds, or integrations succeeded unless actually run and observed.
