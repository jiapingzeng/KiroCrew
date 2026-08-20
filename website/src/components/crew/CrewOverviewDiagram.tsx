/**
 * The crew overview: what wakes this crew on the left, what it works with on the
 * right, the crew itself in the middle.
 *
 * The two columns exist because the editor's hardest copy problem is that
 * `triggers` (which decides when the orchestrator PICKS this crew for work a
 * human started) and a schedule (which starts a turn with nobody present) read as
 * the same kind of thing when stacked as prose — `CrewWakeSection` currently
 * carries a sentence of disclaimer to say they are not. Direction is the thing
 * being explained, so it is drawn rather than asserted.
 *
 * Connectors are COMPUTED from the two columns' lengths, never hand-placed. That
 * is what makes a new binding free: adding a node re-fans the SVG with no edit
 * here, whereas a diagram with baked coordinates has to be redrawn each time and
 * is why hand-drawn diagrams rot.
 */
import type { LucideIcon } from 'lucide-react'

/** One box in either column. */
export interface CrewWireNode {
  key: string
  icon: LucideIcon
  /** What kind of thing it is, e.g. "Workspace". */
  label: string
  /** What it currently points at, e.g. `oncall`. */
  value: string
  /** Render the value in the mono face — for identifiers the user can copy. */
  mono?: boolean
  /** Render the value italic and muted — for "Inherited", which is an absence. */
  muted?: boolean
  /** Short status pill on the row's trailing edge. */
  tag?: { tone: 'warn' | 'info'; text: string }
  /** Dashed and dimmed: a real input that carries no crew binding yet. Drawn so
   *  the gap is visible rather than absent. */
  ghost?: boolean
}

/** Row pitch in px. One node box plus its gap; the fan maths depends on it. */
const ROW = 44

const TAG_TONE = {
  warn: 'border-warn text-warn bg-warn-subtle',
  info: 'border-info text-info bg-info-subtle',
} as const

/** Vertical centre of node `i` inside a column of `n`, within a band of height
 *  `h`. Both columns are centred in the same band, so a 3-node column and a
 *  4-node one still meet the hub at the right heights. */
function centreOf(i: number, n: number, h: number): number {
  return (h - n * ROW) / 2 + i * ROW + ROW / 2
}

/** Bezier fan from one point to `n` points (or the reverse when `toHub`). */
function fanPaths(n: number, w: number, h: number, toHub: boolean): string[] {
  const mid = h / 2
  return Array.from({ length: n }, (_, i) => {
    const y = centreOf(i, n, h)
    return toHub
      ? `M0 ${y} C${w * 0.45} ${y} ${w * 0.55} ${mid} ${w} ${mid}`
      : `M0 ${mid} C${w * 0.45} ${mid} ${w * 0.55} ${y} ${w} ${y}`
  })
}

function WireNode({ node, side }: { node: CrewWireNode; side: 'in' | 'out' }) {
  const Icon = node.icon
  const accent = side === 'in' ? 'border-aim/60' : 'border-accent/45'
  const chip = side === 'in' ? 'bg-aim-subtle text-aim' : 'bg-accent-subtle text-accent'
  return (
    <div
      className={[
        'flex items-center gap-2 rounded-lg border bg-bg-elevated px-2.5 py-1.5',
        node.ghost ? 'border-dashed border-border-strong opacity-60' : accent,
      ].join(' ')}
      // Fixed height: the fan maths places curves at ROW pitch, so a box that
      // grew with its content would meet its connector off-centre.
      style={{ height: ROW - 7 }}
      data-testid={`crew-wire-${node.key}`}
    >
      <span
        className={[
          'flex h-[23px] w-[23px] shrink-0 items-center justify-center rounded-md',
          node.ghost ? 'bg-bg-hover text-muted' : chip,
        ].join(' ')}
      >
        <Icon className="lucide-inline h-[13px] w-[13px]" aria-hidden="true" />
      </span>
      <span className="min-w-0 flex-1">
        <span className="block truncate text-[9px] uppercase tracking-[0.07em] leading-tight text-muted">
          {node.label}
        </span>
        {/* `pr-0.5` with `truncate`: an italic glyph leans past its own advance
            width and `overflow:hidden` clips the overhang instead of eliding. */}
        <span
          className={[
            'block truncate pr-0.5 text-[12px] leading-tight',
            node.muted ? 'italic text-muted' : 'text-text-strong',
            node.mono && !node.muted ? 'font-mono' : '',
          ].join(' ')}
        >
          {node.value}
        </span>
      </span>
      {node.tag && (
        <span className={`shrink-0 rounded border px-1.5 text-[10px] ${TAG_TONE[node.tag.tone]}`}>
          {node.tag.text}
        </span>
      )}
    </div>
  )
}

export interface CrewOverviewDiagramProps {
  inputs: CrewWireNode[]
  outputs: CrewWireNode[]
  /** Heading over the left column. */
  inputsLabel: string
  /** Heading over the right column. */
  outputsLabel: string
  /** The crew's avatar, rendered in the hub. */
  hub: React.ReactNode
}

export default function CrewOverviewDiagram({
  inputs, outputs, inputsLabel, outputsLabel, hub,
}: CrewOverviewDiagramProps) {
  const band = Math.max(inputs.length, outputs.length, 1) * ROW
  const inFan = fanPaths(inputs.length, 56, band, true)
  const outFan = fanPaths(outputs.length, 36, band, false)

  const column = (nodes: CrewWireNode[], side: 'in' | 'out', label: string) => (
    <div className="min-w-0">
      <div className="mb-1.5 text-[9px] uppercase tracking-[0.08em] text-muted-strong">{label}</div>
      <div className="flex flex-col gap-[7px]">
        {nodes.map(n => <WireNode key={n.key} node={n} side={side} />)}
      </div>
    </div>
  )

  return (
    // Narrow-first: below `sm` the two columns stack and the connectors are
    // dropped, because a curve between vertically stacked boxes states a
    // direction the layout no longer has. The wide layout is the grid.
    <div
      className="flex flex-col gap-4 sm:grid sm:items-center
                 sm:grid-cols-[minmax(0,1fr)_56px_auto_36px_minmax(0,1fr)] sm:gap-0"
      data-testid="crew-overview-diagram"
    >
      {column(inputs, 'in', inputsLabel)}
      <svg
        viewBox={`0 0 56 ${band}`}
        width={56}
        height={band}
        className="hidden overflow-visible sm:block"
        aria-hidden="true"
        data-testid="crew-wire-fan-in"
      >
        <g fill="none" stroke="var(--aim)" strokeWidth={1.2} opacity={0.85}>
          {inFan.map((d, i) => (
            <path key={i} d={d} {...(inputs[i]?.ghost ? { strokeDasharray: '3 3', opacity: 0.5 } : {})} />
          ))}
        </g>
      </svg>
      <div className="mx-auto flex h-16 w-16 items-center justify-center rounded-full border border-accent
                      bg-bg-elevated">
        {hub}
      </div>
      <svg
        viewBox={`0 0 36 ${band}`}
        width={36}
        height={band}
        className="hidden overflow-visible sm:block"
        aria-hidden="true"
        data-testid="crew-wire-fan-out"
      >
        <g fill="none" stroke="var(--accent)" strokeWidth={1.2} opacity={0.85}>
          {outFan.map((d, i) => <path key={i} d={d} />)}
        </g>
      </svg>
      {column(outputs, 'out', outputsLabel)}
    </div>
  )
}
