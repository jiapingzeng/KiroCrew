/**
 * The crew editor's vertical rail.
 *
 * Renders whatever `useCrewEditorSections` returns, so a new surface never edits
 * this file. Group headings come from consecutive rows sharing a `group`, which
 * means the registry's ORDER defines the grouping and there is no second list to
 * keep in step with it.
 *
 * Keyboard behaviour is the WAI-ARIA tabs pattern under a roving tabindex, so the
 * whole rail is one Tab stop rather than one per surface — and the index maths is
 * reused from `UnderlineTabs` rather than reimplemented, because "skip a disabled
 * row, wrap at the ends" is exactly the same problem there.
 */
import { useRef } from 'react'
import { nextEnabledIndex, edgeEnabledIndex } from '../UnderlineTabs'
import type { CrewEditorSection, CrewPaneKey, CrewRailTone } from './crewEditorSections'

const TONE_DOT: Record<CrewRailTone, string> = {
  ok: 'bg-ok',
  warn: 'bg-warn',
  info: 'bg-info',
}

export interface CrewEditorRailProps {
  sections: CrewEditorSection[]
  value: CrewPaneKey
  onChange: (key: CrewPaneKey) => void
  /** Names the rail for assistive tech — the dialog holds no other tablist, but
   *  an unlabelled one is still announced without saying what it navigates. */
  ariaLabel: string
  /** Prefix for the `aria-controls` id each row points at. */
  panelIdPrefix: string
}

export default function CrewEditorRail({
  sections, value, onChange, ariaLabel, panelIdPrefix,
}: CrewEditorRailProps) {
  const refs = useRef<Array<HTMLButtonElement | null>>([])

  // `nextEnabledIndex` takes `UnderlineTab`s; only `key` and `disabled` are read,
  // and projecting avoids widening a shared component's signature for one caller.
  const nav = sections.map(s => ({ key: s.key, label: s.label, disabled: s.disabled }))

  const move = (to: number) => {
    const target = sections[to]
    if (!target || target.disabled) return
    onChange(target.key)
    // Focus follows selection so the next arrow press continues from the row the
    // user actually landed on.
    refs.current[to]?.focus()
  }

  const onKeyDown = (e: React.KeyboardEvent, i: number) => {
    if (e.key === 'ArrowDown' || e.key === 'ArrowRight') {
      e.preventDefault()
      move(nextEnabledIndex(nav, i, 1))
    } else if (e.key === 'ArrowUp' || e.key === 'ArrowLeft') {
      e.preventDefault()
      move(nextEnabledIndex(nav, i, -1))
    } else if (e.key === 'Home') {
      e.preventDefault()
      move(edgeEnabledIndex(nav, 'first'))
    } else if (e.key === 'End') {
      e.preventDefault()
      move(edgeEnabledIndex(nav, 'last'))
    }
  }

  let lastGroup = ''
  return (
    <div
      role="tablist"
      aria-orientation="vertical"
      aria-label={ariaLabel}
      // 200px from `sm` up, matching the Capabilities page's own tab sidebar:
      // narrower clipped the two-part "Workspace · Memory" row, and a truncated
      // navigation label is the one place truncation is not acceptable.
      //
      // At phone widths it lays across the top instead and scrolls sideways. A
      // 200px rail against a 420px viewport leaves the pane too narrow to hold a
      // select, and this dialog is reachable at 320px.
      className="flex shrink-0 gap-px overflow-x-auto border-b border-border bg-bg-accent p-2
                 sm:w-[200px] sm:flex-col sm:overflow-x-visible sm:border-b-0 sm:border-r"
    >
      {sections.map((s, i) => {
        const Icon = s.icon
        const isActive = s.key === value
        const isDisabled = s.disabled === true
        const heading = !s.foot && s.group && s.group !== lastGroup ? s.group : ''
        if (heading) lastGroup = s.group
        return (
          <div key={s.key} className={s.foot ? 'sm:mt-auto sm:pt-2' : undefined}>
            {heading && (
              <div className="hidden px-2 pb-1 pt-2.5 text-[9.5px] uppercase tracking-[0.08em]
                              text-muted-strong sm:block">
                {heading}
              </div>
            )}
            <button
              ref={el => {
                refs.current[i] = el
              }}
              type="button"
              role="tab"
              aria-selected={isActive}
              aria-disabled={isDisabled || undefined}
              // Roving tabindex. A disabled row stays reachable by arrow key so
              // its reason is readable, but never becomes the single Tab stop.
              tabIndex={isActive ? 0 : -1}
              title={s.reason || s.label}
              aria-controls={`${panelIdPrefix}-${s.key}`}
              data-testid={`crew-rail-${s.key}`}
              onClick={() => {
                if (!isDisabled) onChange(s.key)
              }}
              onKeyDown={e => onKeyDown(e, i)}
              className={[
                'flex w-full items-center gap-2.5 whitespace-nowrap rounded-md px-2 py-1.5',
                'text-left text-[12.5px] focus-ring',
                isDisabled ? 'cursor-default opacity-40' : 'hover:bg-bg-hover',
                isActive ? 'bg-bg-hover text-text-strong' : 'text-muted',
                s.foot ? 'text-danger' : '',
              ].join(' ')}
            >
              <Icon className="lucide-inline h-[13px] w-[13px] shrink-0" aria-hidden="true" />
              <span className="min-w-0 flex-1 truncate">{s.label}</span>
              {s.tone && (
                <span
                  className={`h-1.5 w-1.5 shrink-0 rounded-full ${TONE_DOT[s.tone]}`}
                  aria-hidden="true"
                />
              )}
              {s.count && (
                <span className="shrink-0 text-[10px] tabular-nums text-muted-strong">{s.count}</span>
              )}
              {isDisabled && <span className="shrink-0 text-[10px] text-muted-strong">—</span>}
            </button>
          </div>
        )
      })}
    </div>
  )
}
