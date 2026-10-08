/**
 * "N tabs were not restored" becomes pixels, and says nothing when it should not.
 *
 * A tab the gateway's startup restore lists and cannot show leaves no other trace a
 * person can see: the session is intact, nothing was closed and nothing was deleted,
 * so the sidebar simply has fewer rows than before the restart. The only way to
 * notice was to remember what used to be there.
 *
 * What this file pins is the discipline around that notice rather than its wording:
 * it renders through the canonical error surface, an unanswered read is not a
 * reported zero and keeps asking, a gateway restart re-asks, a dismissal survives a
 * reload of the same browser tab but a LARGER loss still speaks up, and a read that
 * fails cannot take the chat pane down with it -- the notice sits on the transcript's
 * render path, where an escaping error costs the user the conversation.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, act } from '@testing-library/react'
import { Provider } from 'react-redux'
import { configureStore } from '@reduxjs/toolkit'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'

const reads = vi.hoisted(() => ({ impl: vi.fn() }))
vi.mock('../api/client', () => ({
  api: { get chatSlotsUnrestored() { return reads.impl } },
  SEARCH_MIN_CHARS: 2,
}))

import dashboardReducer, { sseConnected, sseDisconnected } from '../store/dashboardSlice'
import { UnrestoredTabsNotice } from '../pages/chat/page/ChatPaneNotices'
import { i18nT } from '../i18n/t'

const NOTICE = 'unrestored-tabs-notice'

/** The notice reads the WebSocket's connected flag, so it needs the real slice. */
function makeStore(connected = true) {
  const store = configureStore({ reducer: { dashboard: dashboardReducer } })
  if (!connected) store.dispatch(sseDisconnected())
  return store
}

function mount(store = makeStore()) {
  // retry off: the shared ladder is the production behaviour this hook opts into,
  // but a test asserting the failure path must not wait out its backoff.
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const wrap = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>
      <Provider store={store}>{children}</Provider>
    </QueryClientProvider>
  )
  return { store, ...render(<UnrestoredTabsNotice />, { wrapper: wrap }) }
}

describe('the unrestored-tabs notice', () => {
  beforeEach(() => {
    sessionStorage.clear()
    reads.impl = vi.fn()
  })
  afterEach(() => { vi.useRealTimers() })

  it('renders through ErrorNotice, not a hand-written status box', async () => {
    reads.impl.mockResolvedValue({ reported: true, count: 16 })
    mount()
    const notice = await screen.findByTestId(NOTICE)
    expect(notice.textContent).toContain('16')
    // `errors-use-error-notice`: a failed restore is an error, and toning it down to
    // a polite status drops the error affordances and the agent hand-off with it.
    expect(screen.getByTestId('unrestored-tabs-error')).toBeTruthy()
    expect(notice.querySelector('[role="status"]')).toBeNull()
    // The remedy: the pane that lists every session by name, which is where a tab
    // can be identified and reopened.
    expect(notice.querySelector('a[href="/chat?history=1"]')).toBeTruthy()
  })

  it('says nothing when the restore reports no drops', async () => {
    reads.impl.mockResolvedValue({ reported: true, count: 0 })
    mount()
    await waitFor(() => expect(reads.impl).toHaveBeenCalled())
    expect(screen.queryByTestId(NOTICE)).toBeNull()
  })

  it('keeps asking while the restore has not answered, then renders the answer', async () => {
    // The restore runs AFTER the gateway starts serving, so an early arrival reads
    // `reported: false`. Rendering that as zero would be silent about a real loss.
    // Fake timers drive the poll, and every wait here is an explicit `act` flush
    // rather than `waitFor`: `waitFor` schedules its own retries on the timers this
    // test controls, so it never advances and the test times out instead of failing.
    vi.useFakeTimers()
    reads.impl = vi
      .fn()
      .mockResolvedValueOnce({ reported: false, count: 0 })
      .mockResolvedValue({ reported: true, count: 4 })
    mount()
    await act(async () => {})
    expect(reads.impl).toHaveBeenCalledTimes(1)
    expect(screen.queryByTestId(NOTICE)).toBeNull()

    await act(async () => { await vi.advanceTimersByTimeAsync(5000) })
    expect(reads.impl.mock.calls.length).toBeGreaterThanOrEqual(2)
    expect(screen.getByTestId(NOTICE).textContent).toContain('4')

    // And it STOPS once answered: an answer is settled for the life of the process.
    const settled = reads.impl.mock.calls.length
    await act(async () => { await vi.advanceTimersByTimeAsync(30_000) })
    expect(reads.impl.mock.calls.length).toBe(settled)
  })

  it('re-asks when the socket reconnects, because a new gateway ran its own restore', async () => {
    reads.impl = vi
      .fn()
      .mockResolvedValueOnce({ reported: true, count: 0 })
      .mockResolvedValue({ reported: true, count: 7 })
    const { store } = mount()
    await waitFor(() => expect(reads.impl).toHaveBeenCalledTimes(1))
    expect(screen.queryByTestId(NOTICE)).toBeNull()

    act(() => { store.dispatch(sseDisconnected()) })
    act(() => { store.dispatch(sseConnected()) })

    await waitFor(() => expect(screen.queryByTestId(NOTICE)).not.toBeNull())
    expect(screen.getByTestId(NOTICE).textContent).toContain('7')
  })

  it('does not re-ask on the first connected render, which is not a reconnect', async () => {
    reads.impl.mockResolvedValue({ reported: true, count: 2 })
    mount()
    await screen.findByTestId(NOTICE)
    await waitFor(() => expect(reads.impl).toHaveBeenCalledTimes(1))
  })

  it('stays dismissed for the same count and returns for a larger one', async () => {
    reads.impl.mockResolvedValue({ reported: true, count: 3 })
    const first = mount()
    await screen.findByTestId(NOTICE)
    // By accessible name, resolved through the same catalog call the component
    // makes, so the two cannot drift. ErrorNotice's dismiss control carries no
    // testid of its own.
    const dismissName = i18nT('pages.chatPage.dismiss_until_next_visit')
    act(() => { screen.getByRole('button', { name: dismissName }).click() })
    await waitFor(() => expect(screen.queryByTestId(NOTICE)).toBeNull())
    first.unmount()

    // Same count, same browser session: the user already answered.
    const again = mount()
    await waitFor(() => expect(reads.impl).toHaveBeenCalledTimes(2))
    expect(screen.queryByTestId(NOTICE)).toBeNull()
    again.unmount()

    // A later, larger loss is a different fact and must speak up.
    reads.impl.mockResolvedValue({ reported: true, count: 9 })
    mount()
    expect((await screen.findByTestId(NOTICE)).textContent).toContain('9')
  })

  it('renders nothing when the read throws synchronously', async () => {
    // A cached bundle whose `api` predates this method, or a host supplying a
    // narrower one. The notice is a courtesy; it may not become an outage.
    reads.impl = vi.fn(() => { throw new TypeError('not a function') })
    expect(() => mount()).not.toThrow()
    await waitFor(() => expect(reads.impl).toHaveBeenCalled())
    expect(screen.queryByTestId(NOTICE)).toBeNull()
  })

  it('renders nothing when the read rejects', async () => {
    reads.impl.mockRejectedValue(new Error('offline'))
    mount()
    await waitFor(() => expect(reads.impl).toHaveBeenCalled())
    expect(screen.queryByTestId(NOTICE)).toBeNull()
  })
})
