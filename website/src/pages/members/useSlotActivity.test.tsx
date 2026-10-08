import { describe, expect, it } from 'vitest'
import { renderHook } from '@testing-library/react'
import { Provider } from 'react-redux'
import { configureStore } from '@reduxjs/toolkit'
import type { ReactNode } from 'react'
import chatReducer, { setSlotStatusDetail, sseToolActivity, sseToolResult, syncSlotRunningFromServer } from '../../store/chatSlice'
import dashboardReducer from '../../store/dashboardSlice'
import { useSlotActivity } from './useSlotActivity'

const SLOT = 'member-radar'

function makeStore() {
  return configureStore({ reducer: { chat: chatReducer, dashboard: dashboardReducer } })
}

function run(store: ReturnType<typeof makeStore>, opts: Parameters<typeof useSlotActivity>[1]) {
  const wrapper = ({ children }: { children: ReactNode }) => <Provider store={store}>{children}</Provider>
  return renderHook(() => useSlotActivity(SLOT, opts), { wrapper }).result.current
}

const call = (store: ReturnType<typeof makeStore>, id: string, tool: string, purpose: string, identity?: { tool_name: string; mcp_server: string }) => {
  store.dispatch(syncSlotRunningFromServer({ slot: SLOT, running: true, stopping: false }))
  store.dispatch(sseToolActivity({ slot: SLOT, tool, kind: 'other', purpose, input_preview: '', tool_call_id: id, ...identity }))
  store.dispatch(setSlotStatusDetail({ slot: SLOT, kind: 'tool', purpose, toolName: tool, toolCallId: id, ts: 1 }))
}

describe('useSlotActivity (the header pill and the chat status line share this reading)', () => {
  it('idle when the slot is not running', () => {
    expect(run(makeStore(), { running: false })).toEqual({ kind: 'idle' })
  })

  it('names the open tool call by the shared status label', () => {
    const store = makeStore()
    call(store, 'tc-1', 'gh issue list', 'Checking the triage queue')
    expect(run(store, { running: true })).toEqual({ kind: 'tool', text: 'Checking the triage queue' })
  })

  it('reads as thinking once the call has returned (its output is in the tool log)', () => {
    const store = makeStore()
    call(store, 'tc-1', 'gh issue list', 'Checking the triage queue')
    store.dispatch(sseToolResult({ slot: SLOT, output: '', tool_call_id: 'tc-1' }))
    expect(run(store, { running: true }).kind).toBe('thinking')
  })

  it('with hideQuietEnd, a nothing_to_do call reads as thinking even while in flight (identity, not words)', () => {
    const store = makeStore()
    call(store, 'tc-q', '@kirocrew-core/nothing_to_do', 'Nothing to report', { tool_name: 'nothing_to_do', mcp_server: 'kirocrew-core' })
    expect(run(store, { running: true, hideQuietEnd: true }).kind).toBe('thinking')
    // The header pill (no option) still names it — this is the chat line's rule only.
    expect(run(store, { running: true })).toEqual({ kind: 'tool', text: 'Nothing to report' })
    // A shell whose TITLE says nothing_to_do is an ordinary step.
    const other = makeStore()
    call(other, 'tc-s', 'echo nothing_to_do', 'Echo the name', { tool_name: 'execute_bash', mcp_server: '' })
    expect(run(other, { running: true, hideQuietEnd: true })).toEqual({ kind: 'tool', text: 'Echo the name' })
  })
})
