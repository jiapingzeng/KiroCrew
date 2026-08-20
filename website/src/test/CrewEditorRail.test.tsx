/**
 * Guards on the crew editor's rail.
 *
 * Two properties are worth pinning, and neither is visual. First, the rail is
 * generated from the registry, so the GROUPING follows the registry's order and a
 * new surface needs no layout edit — a test that hard-codes the row list would
 * defeat the thing it is meant to protect, so these assert the derivation
 * instead. Second, the disabled row: it exists to make a known gap visible, which
 * only works if it stays reachable by keyboard and refuses selection.
 */
import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { Boxes, Clock, LayoutDashboard, Trash2, Webhook } from 'lucide-react'

import CrewEditorRail from '../components/crew/CrewEditorRail'
import type { CrewEditorSection } from '../components/crew/crewEditorSections'

const SECTIONS: CrewEditorSection[] = [
  { key: 'overview', group: 'Who it is', icon: LayoutDashboard, label: 'Overview' },
  { key: 'template', group: 'What it can do', icon: Boxes, label: 'Agent Template' },
  { key: 'schedules', group: 'When it works', icon: Clock, label: 'Schedules', count: '2/3' },
  {
    key: 'webhook',
    group: 'When it works',
    icon: Webhook,
    label: 'Webhook',
    disabled: true,
    reason: 'Tokens carry no crew binding yet.',
  },
  { key: 'danger', group: '', icon: Trash2, label: 'Danger zone', foot: true },
]

function renderRail(value: CrewEditorSection['key'] = 'overview') {
  const onChange = vi.fn()
  render(
    <CrewEditorRail
      sections={SECTIONS}
      value={value}
      onChange={onChange}
      ariaLabel="Crew settings"
      panelIdPrefix="pane"
    />,
  )
  return { onChange }
}

describe('crew editor rail — structure from the registry', () => {
  it('emits one heading per group, and none for a footer row', () => {
    renderRail()
    // "When it works" covers two consecutive rows and is written once; the footer
    // row carries no group, so no heading is invented for it.
    for (const g of ['Who it is', 'What it can do', 'When it works']) {
      expect(screen.getAllByText(g)).toHaveLength(1)
    }
    const headings = screen.getByRole('tablist').querySelectorAll('.uppercase')
    expect(headings).toHaveLength(3)
  })

  it('renders a row per registry entry, in registry order', () => {
    renderRail()
    const labels = screen.getAllByRole('tab').map(el => el.textContent?.trim())
    expect(labels?.[0]).toContain('Overview')
    expect(labels?.[labels.length - 1]).toContain('Danger zone')
    expect(screen.getAllByRole('tab')).toHaveLength(SECTIONS.length)
  })

  it('shows a count when the registry supplies one, and nothing when it does not', () => {
    renderRail()
    expect(screen.getByTestId('crew-rail-schedules')).toHaveTextContent('2/3')
    expect(screen.getByTestId('crew-rail-overview').textContent).toBe('Overview')
  })
})

describe('crew editor rail — selection', () => {
  it('names the tablist and marks only the active row selected', () => {
    renderRail('template')
    expect(screen.getByRole('tablist', { name: 'Crew settings' })).toBeInTheDocument()
    expect(screen.getByTestId('crew-rail-template')).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByTestId('crew-rail-overview')).toHaveAttribute('aria-selected', 'false')
  })

  it('keeps the whole rail to one Tab stop via a roving tabindex', () => {
    renderRail('schedules')
    expect(screen.getByTestId('crew-rail-schedules')).toHaveAttribute('tabindex', '0')
    for (const key of ['overview', 'template', 'webhook', 'danger']) {
      expect(screen.getByTestId(`crew-rail-${key}`)).toHaveAttribute('tabindex', '-1')
    }
  })

  it('points each row at the panel it controls', () => {
    renderRail()
    expect(screen.getByTestId('crew-rail-template')).toHaveAttribute('aria-controls', 'pane-template')
  })

  it('selects on click', () => {
    const { onChange } = renderRail()
    fireEvent.click(screen.getByTestId('crew-rail-schedules'))
    expect(onChange).toHaveBeenCalledWith('schedules')
  })
})

describe('crew editor rail — the disabled row is visible, not selectable', () => {
  it('marks it aria-disabled and surfaces the reason rather than staying mute', () => {
    renderRail()
    const row = screen.getByTestId('crew-rail-webhook')
    expect(row).toHaveAttribute('aria-disabled', 'true')
    expect(row).toHaveAttribute('title', 'Tokens carry no crew binding yet.')
  })

  it('refuses a click on it', () => {
    const { onChange } = renderRail()
    fireEvent.click(screen.getByTestId('crew-rail-webhook'))
    expect(onChange).not.toHaveBeenCalled()
  })

  it('arrow keys step over it instead of stranding the caret on it', () => {
    const { onChange } = renderRail('schedules')
    // schedules -> (webhook is disabled) -> danger
    fireEvent.keyDown(screen.getByTestId('crew-rail-schedules'), { key: 'ArrowDown' })
    expect(onChange).toHaveBeenCalledWith('danger')
  })

  it('walks backwards too', () => {
    const { onChange } = renderRail('schedules')
    fireEvent.keyDown(screen.getByTestId('crew-rail-schedules'), { key: 'ArrowUp' })
    expect(onChange).toHaveBeenCalledWith('template')
  })

  it('jumps to the first and last enabled row on Home and End', () => {
    const { onChange } = renderRail('template')
    fireEvent.keyDown(screen.getByTestId('crew-rail-template'), { key: 'Home' })
    expect(onChange).toHaveBeenCalledWith('overview')
    fireEvent.keyDown(screen.getByTestId('crew-rail-template'), { key: 'End' })
    expect(onChange).toHaveBeenCalledWith('danger')
  })
})
