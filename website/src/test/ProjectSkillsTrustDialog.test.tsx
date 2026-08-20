import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

/* ── Mock api/client BEFORE the component imports ── */
const mockApi = vi.hoisted(() => ({
  grantSkillTrust: vi.fn(),
}))
vi.mock('../api/client', () => ({ api: mockApi }))

import ProjectSkillsTrustDialog from '../components/ProjectSkillsTrustDialog'

function Harness(props: Partial<React.ComponentProps<typeof ProjectSkillsTrustDialog>> = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return (
    <QueryClientProvider client={qc}>
      <ProjectSkillsTrustDialog
        open
        skillLeaf="oncall-handover"
        slotKey="dashboard:chat-7"
        onClose={vi.fn()}
        onTrusted={vi.fn()}
        {...props}
      />
    </QueryClientProvider>
  )
}

beforeEach(() => {
  // clearAllMocks (not just restoreAllMocks): these are vi.hoisted vi.fn()s, so
  // restore leaves their CALL HISTORY intact and a `not.toHaveBeenCalled`
  // assertion would read a previous test's call.
  vi.clearAllMocks()
  mockApi.grantSkillTrust.mockResolvedValue({ trusted: true })
})

describe('ProjectSkillsTrustDialog', () => {
  it('renders nothing when closed', () => {
    render(<Harness open={false} />)
    expect(screen.queryByText(/Trust this project's skills\?/)).not.toBeInTheDocument()
  })

  it('names the skill the operator was trying to use', async () => {
    render(<Harness />)
    expect(await screen.findByText(/\$oncall-handover/)).toBeInTheDocument()
  })

  it('states what trusting the folder allows', async () => {
    render(<Harness />)
    // The consent must name the CONSEQUENCE, not the mechanism.
    expect(await screen.findByText(/instruct the agent to run commands/)).toBeInTheDocument()
  })

  it('states what declining does, so the safe choice is not unexplained', async () => {
    render(<Harness />)
    expect(await screen.findByText(/nothing is loaded/)).toBeInTheDocument()
  })

  it('tells the operator the grant can be withdrawn later', async () => {
    render(<Harness />)
    expect(await screen.findByText(/withdraw this later/)).toBeInTheDocument()
  })

  it('grants for the requesting slot and reports the leaf back', async () => {
    const onTrusted = vi.fn()
    render(<Harness onTrusted={onTrusted} />)
    fireEvent.click(await screen.findByRole('button', { name: /Trust this folder/ }))
    await waitFor(() => expect(mockApi.grantSkillTrust).toHaveBeenCalledWith('dashboard:chat-7'))
    await waitFor(() => expect(onTrusted).toHaveBeenCalledWith('oncall-handover'))
  })

  it('declining closes without granting anything', async () => {
    const onClose = vi.fn()
    const onTrusted = vi.fn()
    render(<Harness onClose={onClose} onTrusted={onTrusted} />)
    fireEvent.click(await screen.findByRole('button', { name: /Not now/ }))
    expect(onClose).toHaveBeenCalled()
    expect(mockApi.grantSkillTrust).not.toHaveBeenCalled()
    expect(onTrusted).not.toHaveBeenCalled()
  })

  it('surfaces the server reason when the grant is refused', async () => {
    // Duck-typed unwrap: the real ApiError carries `body`, and an instanceof
    // check would read false under a partial mock or across bundle realms.
    mockApi.grantSkillTrust.mockRejectedValue({
      status: 400,
      body: JSON.stringify({ error: 'no project is set for this chat' }),
    })
    const onTrusted = vi.fn()
    render(<Harness onTrusted={onTrusted} />)
    fireEvent.click(await screen.findByRole('button', { name: /Trust this folder/ }))
    expect(await screen.findByRole('alert')).toHaveTextContent('no project is set for this chat')
    // A refused grant must not be reported as consent.
    expect(onTrusted).not.toHaveBeenCalled()
  })

  it('falls back to a generic message when the failure carries no reason', async () => {
    mockApi.grantSkillTrust.mockRejectedValue({ status: 500 })
    render(<Harness />)
    fireEvent.click(await screen.findByRole('button', { name: /Trust this folder/ }))
    expect(await screen.findByRole('alert')).toHaveTextContent(/Could not record trust/)
  })

  it('accepts an object error body as well as a JSON string', async () => {
    mockApi.grantSkillTrust.mockRejectedValue({ body: { error: 'unusable project' } })
    render(<Harness />)
    fireEvent.click(await screen.findByRole('button', { name: /Trust this folder/ }))
    expect(await screen.findByRole('alert')).toHaveTextContent('unusable project')
  })
})
