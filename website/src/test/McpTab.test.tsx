import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { McpServer } from '../types'

/* ── Mocks: must run before importing the component ── */
const mockApi = vi.hoisted(() => ({
  mcpServers: vi.fn(),
  mcpDiscover: vi.fn(),
  mcpProbe: vi.fn(),
  mcpApply: vi.fn(),
  mcpGlobalScopes: vi.fn(),
}))
vi.mock('../api/client', () => ({ api: mockApi }))

vi.mock('../providers', () => ({
  useProvider: () => ({ displayName: 'kiro', labels: { pluginRegistryName: 'Packages' } }),
}))

// The modal has its own suite (McpBrowserModal.test.tsx) — probe only the
// open/close wiring here.
vi.mock('../components/McpBrowserModal', () => ({
  default: ({ open }: { open: boolean }) => (
    <div data-testid="mcp-browser-modal" data-open={String(open)} />
  ),
}))

import McpTab from '../pages/overview/McpTab'
import { MemoryRouter } from 'react-router-dom'

const server = (name: string): McpServer => ({
  name, command: `${name}-cmd`, status: 'ok', source: 'kirocrew', enabled: true, tools: ['t1'],
})

function renderTab() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  // MemoryRouter because the sign-in guidance renders a <Link> to the chat route:
  // react-router's Link reads its context unconditionally and throws without a
  // router, so this wrapper is load-bearing rather than boilerplate.
  return render(
    <MemoryRouter>
      <QueryClientProvider client={qc}><McpTab /></QueryClientProvider>
    </MemoryRouter>,
  )
}

beforeEach(() => {
  Object.values(mockApi).forEach(m => m.mockReset())
  mockApi.mcpServers.mockResolvedValue([server('alpha'), server('beta')])
  mockApi.mcpGlobalScopes.mockResolvedValue({ scopes: [] })
})

describe('McpTab restructure', () => {
  it('header shows MCP Servers with the installed count', async () => {
    renderTab()
    await waitFor(() => expect(screen.getByText('MCP Servers (2)')).toBeInTheDocument())
  })

  it('the inline registry card is gone', async () => {
    renderTab()
    await waitFor(() => expect(screen.getByText('MCP Servers (2)')).toBeInTheDocument())
    expect(screen.queryByText('Browse Integrations')).not.toBeInTheDocument()
    expect(screen.queryByText('Installed Integrations')).not.toBeInTheDocument()
  })

  it('Add Server button opens the browser modal', async () => {
    renderTab()
    const addBtn = await screen.findByRole('button', { name: /Add Server/ })
    expect(screen.getByTestId('mcp-browser-modal')).toHaveAttribute('data-open', 'false')
    fireEvent.click(addBtn)
    expect(screen.getByTestId('mcp-browser-modal')).toHaveAttribute('data-open', 'true')
  })

  it('keeps the installed-servers table as the page body', async () => {
    renderTab()
    // Both configured servers render as table rows (name in a <code> cell —
    // the status badge chips also contain the name, so scope the query).
    await waitFor(() => expect(screen.getByText('alpha', { selector: 'code' })).toBeInTheDocument())
    expect(screen.getByText('beta', { selector: 'code' })).toBeInTheDocument()
    expect(screen.getByText('alpha-cmd')).toBeInTheDocument()
    // Uninstall stays in the table (per-row action), not in the modal.
    expect(screen.getAllByRole('button', { name: 'Uninstall' })).toHaveLength(2)
  })

  it('badges a registry-managed remote server', async () => {
    mockApi.mcpServers.mockResolvedValue([{
      ...server('notion'),
      command: '',
      url: 'https://mcp.notion.com/mcp',
    }])
    renderTab()
    await waitFor(() => expect(screen.getByText('Managed by Connections')).toBeInTheDocument())
  })
})

/**
 * #1853: the status probe runs without the OAuth token kiro-cli holds, so a
 * remote OAuth server answers it with 401 while the agent runtime calls the same
 * server fine. The gateway reports that as `needs_auth`, and the table must say
 * only what it knows — the authorization is not visible from here — rather than
 * calling a working server broken or claiming it needs a grant it may already have.
 */
describe('McpTab needs_auth status', () => {
  const remote = (status: string): McpServer => ({
    name: 'atlassian',
    command: '',
    url: 'https://mcp.atlassian.com/v1/sse',
    status,
    source: 'mcp.json',
    enabled: true,
    tools: [],
  })

  it('renders the not-verified state, not an error badge', async () => {
    mockApi.mcpServers.mockResolvedValue([remote('needs_auth')])
    renderTab()

    await waitFor(() => expect(screen.getByText('Not verified')).toBeInTheDocument())
    // The badge carries the warn tone, never the error tone.
    expect(screen.getByText('Not verified').className).toContain('text-warn')
    expect(screen.getByText('Not verified').className).not.toContain('text-danger')
    // Neither the old "Error" label nor the uninformative "Unknown" fallback.
    expect(screen.queryByText('Error')).not.toBeInTheDocument()
    expect(screen.queryByText('Unknown')).not.toBeInTheDocument()
  })

  it('explains the unverifiable status on hover, naming the server', async () => {
    mockApi.mcpServers.mockResolvedValue([remote('needs_auth')])
    renderTab()

    const badge = await screen.findByText('Not verified')
    const hint = badge.getAttribute('title') || ''
    // Says who holds the token and that a working server is still working —
    // the two facts that make the badge honest instead of alarming.
    expect(hint).toContain('atlassian')
    expect(hint).toContain('Kiro CLI')
    expect(hint).toMatch(/cannot see the authorization/)
  })

  /**
   * With a challenge AND an absent runtime grant, "nobody has signed in" is a
   * fact rather than a guess, so the row names the action. Everything below
   * turns on that pair being present — absent evidence must keep the vaguer
   * wording, because an older gateway sends none and its servers may be fine.
   */
  it('says sign-in is required when the server asked for OAuth and no grant exists', async () => {
    mockApi.mcpServers.mockResolvedValue([
      { ...remote('needs_auth'), authChallenge: true, authGrantPresent: false },
    ])
    renderTab()

    await waitFor(() => expect(screen.getByText('Sign-in required')).toBeInTheDocument())
    expect(screen.queryByText('Not verified')).not.toBeInTheDocument()
    // Still a warning, never an error: nothing is broken, it just needs a sign-in.
    expect(screen.getByText('Sign-in required').className).toContain('text-warn')
    expect(screen.getByText('Sign-in required').className).not.toContain('text-danger')
  })

  it('keeps the not-verified wording once a runtime grant exists', async () => {
    mockApi.mcpServers.mockResolvedValue([
      { ...remote('needs_auth'), authChallenge: true, authGrantPresent: true },
    ])
    renderTab()

    await waitFor(() => expect(screen.getByText('Not verified')).toBeInTheDocument())
    expect(screen.queryByText('Sign-in required')).not.toBeInTheDocument()
  })

  it('keeps the not-verified wording when the gateway sent no authorization evidence', async () => {
    // An older gateway, or a 401 with no challenge. Telling this user to sign in
    // would be a guess about a server that may already be working.
    mockApi.mcpServers.mockResolvedValue([remote('needs_auth')])
    renderTab()

    await waitFor(() => expect(screen.getByText('Not verified')).toBeInTheDocument())
    expect(screen.queryByText(/Open a chat session and send a message/)).not.toBeInTheDocument()
  })

  /**
   * The sign-in prompt is raised by Kiro CLI while a session brings its MCP
   * servers up, which happens on a turn. Nothing the dashboard can call from
   * this panel starts that, so the row states where the sign-in happens rather
   * than offering a control that cannot perform it.
   */
  it('tells the user where the sign-in happens, and offers no control that cannot do it', async () => {
    mockApi.mcpServers.mockResolvedValue([
      { ...remote('needs_auth'), authChallenge: true, authGrantPresent: false },
    ])
    renderTab()

    await waitFor(() => expect(screen.getByText('Sign-in required')).toBeInTheDocument())
    // The remedy is an affordance, not an instruction: the one step the user must
    // take is a link to the chat route, since that navigation IS something the
    // panel can perform. A prose-only hint would make the fix for a
    // discoverability bug itself undiscoverable.
    const link = screen.getByRole('link', { name: /Open a chat session/ })
    expect(link).toHaveAttribute('href', '/chat')
    // The flow has an ending. Sending the user to chat without saying what success
    // looks like left them at a row that merely stops saying "Sign-in required" —
    // so the copy names the refresh and the state it lands in, which is "Not
    // verified" rather than a confirmation Kiro Crew cannot honestly give.
    expect(screen.getByText(/refresh this list and the row will change to Not verified/)).toBeInTheDocument()
    // Still no Authorize control: starting the sign-in is not something this
    // panel can do, and a button here would claim an action it cannot perform.
    expect(screen.queryByRole('button', { name: /Authorize/ })).not.toBeInTheDocument()
  })

  it('does not show the sign-in guidance once a runtime grant exists', async () => {
    mockApi.mcpServers.mockResolvedValue([
      { ...remote('needs_auth'), authChallenge: true, authGrantPresent: true },
    ])
    renderTab()

    await waitFor(() => expect(screen.getByText('Not verified')).toBeInTheDocument())
    expect(screen.queryByText(/Open a chat session and send a message/)).not.toBeInTheDocument()
  })

  /**
   * `max-two-buttons-per-row` (website/AUTOSDE.yaml) caps a horizontal action
   * group at two siblings. A managed row that needs a sign-in is where a third
   * action would land, so the cap is asserted there.
   */
  it('keeps the action group at two buttons on a managed row that needs sign-in', async () => {
    mockApi.mcpServers.mockResolvedValue([
      {
        ...remote('needs_auth'),
        kirocrewManaged: true,
        authChallenge: true,
        authGrantPresent: false,
      },
    ])
    renderTab()

    const uninstall = await screen.findByRole('button', { name: /Uninstall/ })
    const group = uninstall.closest('div')
    expect(group).not.toBeNull()
    expect(group!.querySelectorAll('button').length).toBeLessThanOrEqual(2)
  })

  it('tells the user a pasted token cannot satisfy an OAuth server', async () => {
    mockApi.mcpServers.mockResolvedValue([
      {
        ...remote('error'),
        error: 'HTTP 401',
        headers: { Authorization: '[REDACTED: credential]' },
        authChallenge: true,
      },
    ])
    renderTab()

    await waitFor(() => expect(screen.getByText('HTTP 401')).toBeInTheDocument())
    expect(screen.getByText(/static Authorization header cannot satisfy it/)).toBeInTheDocument()
    // The recovery step, not just the diagnosis: without it a misconfigured user
    // has to guess that the header must come out before a sign-in can work — and
    // "remove the header" is only actionable if it names the control that opens it.
    expect(
      screen.getByText(/Remove the header with this row's JSON editor, then sign in from a chat session/),
    ).toBeInTheDocument()
  })

  it('leaves every other status without a hover explanation', async () => {
    mockApi.mcpServers.mockResolvedValue([remote('ok')])
    renderTab()

    const badge = await screen.findByText('Online')
    expect(badge).not.toHaveAttribute('title')
  })

  it('still renders a real failure as an error badge with its message', async () => {
    mockApi.mcpServers.mockResolvedValue([{ ...remote('error'), error: 'HTTP 500' }])
    renderTab()

    await waitFor(() => expect(screen.getByText('Error')).toBeInTheDocument())
    expect(screen.getByText('Error').className).toContain('text-danger')
    expect(screen.getByText('HTTP 500')).toBeInTheDocument()
    expect(screen.queryByText('Not verified')).not.toBeInTheDocument()
  })
})

describe('McpTab declared-vs-handshake status', () => {
  it('a declared server shows "Declared", never the green "Online"', async () => {
    // probeMode 'declared' means the tool list came from the package's own
    // static declaration — nothing spawned the server. Rendering the same green
    // "Online" as a handshake-proven row asserts something no one verified.
    mockApi.mcpServers.mockResolvedValue([
      { ...server('managed'), probeMode: 'declared', probedAt: 1_700_000_000 },
    ])
    renderTab()
    await waitFor(() => expect(screen.getByText('Declared')).toBeInTheDocument())
    expect(screen.queryByText('Online')).not.toBeInTheDocument()
  })

  it('a handshake-proven server still shows "Online"', async () => {
    mockApi.mcpServers.mockResolvedValue([
      { ...server('real'), probeMode: 'handshake', probedAt: 1_700_000_000 },
    ])
    renderTab()
    await waitFor(() => expect(screen.getByText('Online')).toBeInTheDocument())
    expect(screen.queryByText('Declared')).not.toBeInTheDocument()
  })
})
