/**
 * Guards on the overview diagram's connectors.
 *
 * The property under test is the one the design rests on: the connectors are
 * DERIVED from the two columns' lengths. A hand-positioned diagram has to be
 * redrawn for every new binding, and that is how a diagram silently stops
 * matching the data it claims to show — so "one more node yields one more curve,
 * with no edit to the component" is asserted mechanically rather than trusted.
 */
import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { Boxes, Clock, Cpu, Database, FolderOpen, ShieldCheck, Webhook } from 'lucide-react'

import CrewOverviewDiagram, { type CrewWireNode } from '../components/crew/CrewOverviewDiagram'

const INPUTS: CrewWireNode[] = [
  { key: 'schedules', icon: Clock, label: 'Schedules', value: '2' },
  { key: 'webhook', icon: Webhook, label: 'Webhook', value: 'Not bound to a crew', ghost: true },
]
const OUTPUTS: CrewWireNode[] = [
  { key: 'template', icon: Boxes, label: 'Agent Template', value: 'kirocrew', mono: true },
  { key: 'workspace', icon: FolderOpen, label: 'Workspace', value: 'oncall', mono: true },
  { key: 'memory', icon: Database, label: 'Memory Store', value: 'oncall-mem', mono: true },
  { key: 'model', icon: Cpu, label: 'Model', value: 'Inherited', muted: true },
]

function renderDiagram(inputs = INPUTS, outputs = OUTPUTS) {
  const { container } = render(
    <CrewOverviewDiagram
      inputs={inputs}
      outputs={outputs}
      inputsLabel="Who wakes it"
      outputsLabel="What it works with"
      hub={<span data-testid="hub" />}
    />,
  )
  // By test id, not by document order: every lucide icon is an <svg> too, so a
  // positional selector silently measures an icon's paths instead of a fan.
  const fan = (side: 'in' | 'out') =>
    Array.from(container.querySelector(`[data-testid="crew-wire-fan-${side}"]`)
      ?.querySelectorAll('path') ?? [])
  return { container, inPaths: fan('in'), outPaths: fan('out') }
}

describe('crew overview diagram — connectors follow the data', () => {
  it('draws one curve per node on each side', () => {
    const { inPaths, outPaths } = renderDiagram()
    expect(inPaths).toHaveLength(INPUTS.length)
    expect(outPaths).toHaveLength(OUTPUTS.length)
  })

  it('gains a curve when a binding is added, with no change to the component', () => {
    const before = renderDiagram().outPaths.length
    const grown = [
      ...OUTPUTS,
      { key: 'perm', icon: ShieldCheck, label: 'Permission profile', value: 'oncall-limited' },
    ]
    const after = renderDiagram(INPUTS, grown).outPaths.length
    expect(after).toBe(before + 1)
  })

  it('re-fans rather than shifting: every curve ends at a distinct height', () => {
    const { outPaths } = renderDiagram()
    const ends = outPaths.map(p => (p.getAttribute('d') || '').split(' ').slice(-1)[0])
    expect(new Set(ends).size).toBe(outPaths.length)
  })

  it('meets a single hub height on the input side, whichever column is longer', () => {
    // Both fans are computed against the same band, so the input curves converge
    // on one point — the property that lets a 2-node and a 4-node column line up.
    const { inPaths } = renderDiagram()
    const ends = inPaths.map(p => (p.getAttribute('d') || '').split(' ').slice(-1)[0])
    expect(new Set(ends).size).toBe(1)
  })

  it('dashes the connector of a node that is drawn as a known gap', () => {
    const { inPaths } = renderDiagram()
    expect(inPaths[0]).not.toHaveAttribute('stroke-dasharray')
    expect(inPaths[1]).toHaveAttribute('stroke-dasharray')
  })
})

describe('crew overview diagram — content', () => {
  it('labels both columns and renders the hub', () => {
    renderDiagram()
    expect(screen.getByText('Who wakes it')).toBeInTheDocument()
    expect(screen.getByText('What it works with')).toBeInTheDocument()
    expect(screen.getByTestId('hub')).toBeInTheDocument()
  })

  it('renders each node as its kind plus its current value', () => {
    renderDiagram()
    const node = screen.getByTestId('crew-wire-workspace')
    expect(node).toHaveTextContent('Workspace')
    expect(node).toHaveTextContent('oncall')
  })

  it('hides the connectors from assistive tech, since the text carries the meaning', () => {
    const { container } = renderDiagram()
    for (const side of ['in', 'out']) {
      expect(container.querySelector(`[data-testid="crew-wire-fan-${side}"]`))
        .toHaveAttribute('aria-hidden', 'true')
    }
  })

  it('survives an empty column without dividing by zero', () => {
    const { inPaths, outPaths } = renderDiagram([], OUTPUTS)
    expect(inPaths).toHaveLength(0)
    expect(outPaths).toHaveLength(OUTPUTS.length)
  })
})
