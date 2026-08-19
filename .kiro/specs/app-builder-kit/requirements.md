# Requirements — KiroCrew App Builder Kit

## Introduction

The KiroCrew dashboard frontend (`website/src/`) already exposes an `app-sdk/` that defines
the app contract (`AppApi`, `useAppApi`, `useTheme`, `useNavigate`, `useNotify`,
`useAppEvents`, `useChatLauncher`, `ChatPanel`, `messageRenderers`, `protocol/`), with apps
registered through `apps/builtinRegistry.ts`. However, UI building blocks are not
consolidated: the ~15 apps under `apps/` each re-implement their own tables, cards, and
loading/error/empty states, and agent tool-output rendering is hand-rolled (raw
`<mcwidget>` HTML via `WidgetFrame.tsx`; the tool-request preview in `ApprovalCard.tsx` /
`ToolInputPreview.tsx` renders input as a raw `<pre>` args dump).

The **App Builder Kit** is a frontend build-velocity library that layers on the existing
`app-sdk` to let a developer stand up a new app or feature fast, with production-quality
theming, accessibility, i18n, and agent-output rendering by default. It absorbs the earlier
"Generative-UI Tool Component Library" idea as one module (Tool Views).

Scope is the React frontend only. This is not a backend/MCP toolkit, not an `app-sdk`
rewrite, and not a design-system overhaul.

## Requirements

### Requirement 1 — Reusable UI primitives

**User Story:** As an app author, I want themed, accessible UI primitives, so that I stop
re-implementing tables, cards, and state views in every app.

#### Acceptance Criteria
1. WHEN a developer imports a kit primitive (DataTable, Card, EmptyState, LoadingSkeleton, ErrorNotice, Toolbar, SplitLayout, Drawer, StatusBadge, ConfirmDialog) THEN the kit SHALL render it using the dashboard theme CSS variables with no hard-coded colors.
2. WHEN the active theme changes (light, dark, or custom) THEN each primitive SHALL reflect the new theme without additional code.
3. WHEN a primitive renders user-facing text THEN it SHALL use `i18nT()` and the kit SHALL provide catalog keys in all shipped locale files.
4. WHEN a `DataTable` receives rows and columns THEN it SHALL support sorting and pagination without app-level implementation.
5. IF a primitive is interactive THEN it SHALL be keyboard operable and expose appropriate ARIA roles/labels (WCAG AA).

### Requirement 2 — Typed tool-view components (inline)

**User Story:** As an agent (and as an app author), I want typed components that render common tool outputs, so that I don't hand-write `<mcwidget>` HTML per response.

#### Acceptance Criteria
1. WHERE a tool output matches a kit-published schema THE kit SHALL provide a typed component that renders it inline (ChartToolView, TableToolView, MapToolView, ImageToolView, DiffToolView).
2. WHEN a tool invocation is in `input-streaming`, `input-available`, `output-available`, or `output-error` state THEN the component SHALL render a defined view for that state (skeleton, running affordance, result, error surface respectively).
3. WHEN a tool's output shape does not match the component's schema THEN the mismatch SHALL surface as a TypeScript compile-time error.
4. WHEN a `DiffToolView` renders THEN it SHALL reuse the existing `DiffBlock.tsx` and preserve the dashboard's Open-file diff-header affordance.
5. IF no typed component matches a tool part THEN the kit SHALL fall back to the existing `<mcwidget>` rendering with no regression.
6. WHEN a tool view is rendered inline THEN it SHALL be persistable as an artifact (`kind="widget"`) consistent with the artifacts system.

### Requirement 3 — Rich tool-request preview and approval

**User Story:** As an operator, I want a rich preview of a pending tool call before I approve it, so that I can intervene with full context instead of reading a raw args dump.

#### Acceptance Criteria
1. WHEN a pending tool call has a matching tool-view schema THEN the kit SHALL render a rich preview inside `ApprovalCard` / `ToolInputPreview` instead of the raw `<pre>` dump.
2. WHERE a tool-view schema does not match THE approval surface SHALL fall back to the current `ToolInputPreview` `<pre>` behavior.
3. WHEN the operator approves or rejects THEN the decision SHALL flow through the existing `onApprove(decision, pattern?)` callback and the kit SHALL NOT introduce a new approval API path.
4. WHEN an approval decision is submitted from a chat slot THEN it SHALL resolve via the existing slot-scoped `api.approveChatSlot(slot, action, extra)` path in `ChatInput.tsx`.
5. IF the approval controls are rendered THEN they SHALL be keyboard operable (operators batch-approve).

### Requirement 4 — App scaffolding

**User Story:** As an app author, I want a standard way to define and register an app with proven screen layouts, so that I start from a working shape instead of copying another app.

#### Acceptance Criteria
1. WHEN a developer calls `defineApp({ id, icon, routes, permissions })` THEN the kit SHALL register the app with `builtinRegistry` and populate `AppInfo`/`AppPermissions` in a single call.
2. WHERE a new app needs a common layout THE kit SHALL provide ListDetail, Settings, and Dashboard screen templates modeled on the existing meetings app structure.
3. WHEN two apps register the same route THEN the kit SHALL surface the existing seam-collision report rather than silently overwriting.
4. WHEN the scaffold codegen (`scaffold-app <name>`) runs THEN it SHALL emit the app directory and its registry entry (this criterion MAY be deferred to a later milestone).

### Requirement 5 — Data and async helpers

**User Story:** As a feature dev, I want typed fetch/loading/error and live-update helpers, so that I stop re-writing the same async triad.

#### Acceptance Criteria
1. WHEN a developer uses `useAppResource` THEN it SHALL return typed loading, error, and data states over the existing API-client pattern.
2. WHEN a mutation is performed with the kit's mutation helper THEN it SHALL support optimistic update and rollback on failure.
3. WHERE an app subscribes to server events THE kit SHALL provide a live-update hook built on `useAppEvents` that respects `checkSubscribeAllowed`.

### Requirement 6 — Non-regression and CI compliance

**User Story:** As a maintainer, I want the kit to satisfy the repo's CI gates, so that adopting it does not create review or build friction.

#### Acceptance Criteria
1. WHEN a kit component introduces a user-visible surface change THEN the change SHALL ship with committed, SHA-pinned screenshots satisfying the Screenshot Evidence gate.
2. WHEN kit code is added THEN it SHALL NOT introduce copy/paste clones that fail the jscpd 0% threshold.
3. WHEN kit components add user-facing strings THEN the corresponding keys SHALL exist in all locale files (catalogParity).
4. WHEN an existing app is refactored onto the kit THEN it SHALL show no UX regression versus its pre-refactor behavior.

### Requirement 7 — Dogfood and discoverability

**User Story:** As a maintainer, I want the kit proven on a real app and browsable, so that adoption is de-risked and discoverable.

#### Acceptance Criteria
1. WHEN v1 is complete THEN at least one existing app (e.g. meetings or ops-mission-control) SHALL be refactored onto the kit as a dogfood proof.
2. WHERE a developer wants to see available primitives and tool views THE kit SHALL provide a dev-only gallery route rendering each in light, dark, and custom themes.
