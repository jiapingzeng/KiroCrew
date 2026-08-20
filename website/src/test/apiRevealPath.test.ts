import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// Mock clipboard before importing the module that uses it
vi.mock('../utils/clipboard', () => ({ copyToClipboard: vi.fn().mockResolvedValue(undefined) }))

import { api } from '../api/client'
import { copyToClipboard } from '../utils/clipboard'

describe('api.revealPath', () => {
  let fetchSpy: ReturnType<typeof vi.spyOn>
  let alertSpy: ReturnType<typeof vi.spyOn>

  beforeEach(() => {
    vi.clearAllMocks()
    alertSpy = vi.spyOn(globalThis, 'alert').mockImplementation(() => {})
  })

  afterEach(() => {
    fetchSpy.mockRestore()
    alertSpy.mockRestore()
  })

  it('shows no confirmation on a normal (non-copy) success response', async () => {
    fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    )

    const result = await api.revealPath('/some/path')
    expect(result).toEqual({ ok: true })
    expect(copyToClipboard).not.toHaveBeenCalled()
    expect(alertSpy).not.toHaveBeenCalled()
  })

  it('copies to clipboard and shows exactly one confirmation on a copy-fallback response', async () => {
    fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ ok: true, copy: '/remote/path/file.txt' }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    )

    const result = await api.revealPath('/remote/path/file.txt')
    expect(result).toEqual({ ok: true, copy: '/remote/path/file.txt' })
    expect(copyToClipboard).toHaveBeenCalledWith('/remote/path/file.txt')
    expect(copyToClipboard).toHaveBeenCalledTimes(1)
    expect(alertSpy).toHaveBeenCalledTimes(1)
    // The alert message should be the i18n-resolved string (at test time it
    // resolves to the key path itself or the English value depending on the
    // i18n test setup — we just verify exactly one alert fires).
  })

  it('sends the action parameter to the backend', async () => {
    fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    )

    await api.revealPath('/some/file.txt', 'open')
    const [, init] = fetchSpy.mock.calls[0] as [string, RequestInit]
    expect(JSON.parse(init.body as string)).toEqual({ path: '/some/file.txt', action: 'open' })
  })

  it('defaults action to reveal', async () => {
    fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    )

    await api.revealPath('/some/file.txt')
    const [, init] = fetchSpy.mock.calls[0] as [string, RequestInit]
    expect(JSON.parse(init.body as string)).toEqual({ path: '/some/file.txt', action: 'reveal' })
  })
})
