import { describe, it, expect } from 'vitest'
import { createChatState, processV2Record } from '../v2StreamParser'
import { AgentEventType } from '../../../../packages/agent-conversation/src/core/agentEvents/reducer.js'

// Behaviours specific to the Pi data plane that the shared projection fixture
// does not cover. The fixture pins the SDK/Pi equivalence; this pins the parts
// that only exist on the Pi side.

function piEvent(eventType, data = {}) {
  return { record_type: 'pi_event', event_type: eventType, data }
}

function feed(records) {
  const state = createChatState()
  for (const record of records) processV2Record(state, record)
  return state
}

describe('Pi agent event reducer', () => {
  it('produces blocks whose fields blockToToolProp actually reads', () => {
    // blockToToolProp reads block.output and block.is_error. A reducer emitting
    // result/error instead would render every Pi tool call as permanently
    // pending with no output, and no other test would catch it.
    const state = feed([
      piEvent(AgentEventType.RUN_STARTED),
      piEvent(AgentEventType.TURN_STARTED, { turn_id: 'turn-1' }),
      piEvent(AgentEventType.TOOL_STARTED, {
        tool_call_id: 't1',
        tool_name: 'run_sql',
        input: { sql: 'select 1' },
      }),
      piEvent(AgentEventType.TOOL_COMPLETED, { tool_call_id: 't1', output: 'rows: 1', is_error: false }),
    ])

    const block = state.blocks.find((b) => b.type === 'tool_use')
    expect(block).toBeTruthy()
    expect(block.output).toBe('rows: 1')
    expect(block.is_error).toBe(false)
    expect(block).toHaveProperty('inputJson')
    expect(block.result).toBeUndefined()
    expect(block.error).toBeUndefined()
  })

  it('renders a boundary denial as a failed tool call', () => {
    const state = feed([
      piEvent(AgentEventType.TURN_STARTED, { turn_id: 'turn-1' }),
      piEvent(AgentEventType.TOOL_STARTED, { tool_call_id: 't1', tool_name: 'Bash' }),
      piEvent(AgentEventType.TOOL_DENIED, {
        tool_call_id: 't1',
        reason: 'Bash command references absolute path outside workspace: /etc/shadow',
      }),
    ])

    const block = state.blocks.find((b) => b.type === 'tool_use')
    expect(block.is_error).toBe(true)
    expect(block.output).toMatch(/outside workspace/)
    expect(block.status).toBe('done')
  })

  it('treats a suspended run as terminal', () => {
    // Leaving status as 'streaming' would spin the UI forever waiting for
    // events that are never coming.
    const state = feed([
      piEvent(AgentEventType.RUN_STARTED),
      piEvent(AgentEventType.TURN_STARTED, { turn_id: 'turn-1' }),
      piEvent(AgentEventType.CONTENT_DELTA, { content_id: 'c-0', kind: 'answer', delta: 'partial' }),
      piEvent(AgentEventType.RUN_SUSPENDED, { reason: 'awaiting input' }),
    ])

    expect(state.status).toBe('done')
    expect(state.blocks.every((b) => b.status !== 'streaming')).toBe(true)
  })

  it('surfaces a failure message', () => {
    const state = feed([
      piEvent(AgentEventType.RUN_STARTED),
      piEvent(AgentEventType.RUN_FAILED, { error_code: 'PI_RUN_TIMEOUT', message: '单轮执行超时' }),
    ])

    expect(state.status).toBe('error')
    expect(state.errorText).toBe('单轮执行超时')
  })

  it('falls back to the error code when no message is present', () => {
    const state = feed([piEvent(AgentEventType.RUN_FAILED, { error_code: 'SOME_UNKNOWN_ERROR' })])
    expect(state.errorText).toBe('SOME_UNKNOWN_ERROR')
  })

  it('maps CELL_LOSS to diagnostic message when no message is present', () => {
    const state = feed([piEvent(AgentEventType.RUN_FAILED, { error_code: 'CELL_LOSS' })])
    expect(state.errorText).toBe('后台服务异常中断，请联系管理员')
  })

  it('shows a retry notice until the run makes progress', () => {
    const retrying = feed([
      piEvent(AgentEventType.RUN_STARTED),
      piEvent(AgentEventType.TURN_STARTED, { turn_id: 'turn-1' }),
      piEvent(AgentEventType.CONTENT_DELTA, { turn_id: 'turn-1', content_id: 'c1', kind: 'text', delta: 'par' }),
      piEvent(AgentEventType.RUN_RETRYING, { attempt: 3, max_attempts: 10, delay_ms: 4000, error_message: '503' }),
    ])
    expect(retrying.retryText).toBe('模型服务连接异常，正在重试（3/10）…')
    expect(retrying.status).toBe('streaming')
    // The cut-off block is closed so the activity line carrying the notice shows.
    expect(retrying.blocks.every(block => block.status === 'done')).toBe(true)

    const discarded = feed([
      piEvent(AgentEventType.RUN_STARTED),
      piEvent(AgentEventType.TURN_STARTED, { turn_id: 'turn-1' }),
      piEvent(AgentEventType.CONTENT_DELTA, { turn_id: 'turn-1', content_id: 'c-0', kind: 'answer', delta: 'kept' }),
      piEvent(AgentEventType.TURN_COMPLETED, { turn_id: 'turn-1' }),
      piEvent(AgentEventType.TURN_STARTED, { turn_id: 'turn-2' }),
      piEvent(AgentEventType.CONTENT_DELTA, { turn_id: 'turn-2', content_id: 'c-0', kind: 'answer', delta: 'half an' }),
      piEvent(AgentEventType.TURN_COMPLETED, { turn_id: 'turn-2' }),
      piEvent(AgentEventType.RUN_RETRYING, { attempt: 1, max_attempts: 10, discard_turn_id: 'turn-2' }),
      piEvent(AgentEventType.TURN_STARTED, { turn_id: 'turn-3' }),
      piEvent(AgentEventType.CONTENT_DELTA, { turn_id: 'turn-3', content_id: 'c-0', kind: 'answer', delta: 'whole' }),
    ])
    expect(discarded.blocks.map(block => block.content)).toEqual(['kept', 'whole'])

    // The message shares the blocks array with its state; removal must not replace it.
    const state = createChatState()
    const shared = state.blocks
    for (const record of [
      piEvent(AgentEventType.TURN_STARTED, { turn_id: 'turn-1' }),
      piEvent(AgentEventType.CONTENT_DELTA, { turn_id: 'turn-1', content_id: 'c-0', kind: 'answer', delta: 'half' }),
      piEvent(AgentEventType.RUN_RETRYING, { attempt: 1, max_attempts: 10, discard_turn_id: 'turn-1' }),
    ]) processV2Record(state, record)
    expect(state.blocks).toBe(shared)
    expect(shared).toEqual([])
    expect(discarded.turns.map(turn => turn.blocks.map(block => block.content))).toEqual([['kept'], ['whole']])

    const resumed = feed([
      piEvent(AgentEventType.RUN_STARTED),
      piEvent(AgentEventType.RUN_RETRYING, { attempt: 1, max_attempts: 10 }),
      piEvent(AgentEventType.TURN_STARTED, { turn_id: 'turn-2' }),
    ])
    expect(resumed.retryText).toBe('')

    const failed = feed([
      piEvent(AgentEventType.RUN_RETRYING, { attempt: 10, max_attempts: 10 }),
      piEvent(AgentEventType.RUN_FAILED, { error_code: 'PI_MODEL_ERROR', message: '模型服务暂时不可用' }),
    ])
    expect(failed.retryText).toBe('')
    expect(failed.errorText).toBe('模型服务暂时不可用')
  })

  it('tolerates a missing turn.started rather than losing the answer', () => {
    const state = feed([
      piEvent(AgentEventType.RUN_STARTED),
      piEvent(AgentEventType.CONTENT_DELTA, { content_id: 'c-0', kind: 'answer', delta: 'still here' }),
      piEvent(AgentEventType.RUN_COMPLETED, {}),
    ])

    expect(state.blocks.map((b) => b.content)).toEqual(['still here'])
  })

  it('replaces a block with a live snapshot instead of appending to it', () => {
    // A stream opened or reconnected mid-block gets the full text so far as
    // `text`; the client may already show part of it, so appending would duplicate.
    const delta = (d) => piEvent(AgentEventType.CONTENT_DELTA, { turn_id: 'turn-1', content_id: 'c-0', kind: 'answer', ...d })
    const state = feed([
      piEvent(AgentEventType.TURN_STARTED, { turn_id: 'turn-1' }),
      delta({ delta: 'par' }),
      delta({ text: 'partial' }),
      delta({ delta: '!' }),
    ])

    const block = state.blocks.find((b) => b.type === 'text')
    expect(block.content).toBe('partial!')
    expect(block.status).not.toBe('done')
  })

  it('keeps parallel reasoning and answer blocks independently streaming', () => {
    const state = feed([
      piEvent(AgentEventType.TURN_STARTED, { turn_id: 'turn-1' }),
      piEvent(AgentEventType.CONTENT_DELTA, { content_id: 'c-0', kind: 'reasoning', delta: 'thinking...' }),
      piEvent(AgentEventType.CONTENT_DELTA, { content_id: 'c-1', kind: 'answer', delta: 'answer' }),
    ])

    expect(state.blocks.map((b) => b.type)).toEqual(['thinking', 'text'])
    expect(state.blocks.map((b) => b.status)).toEqual(['streaming', 'streaming'])
  })

  it('settles only the content_id named by content.completed', () => {
    const state = createChatState()
    const records = [
      piEvent(AgentEventType.TURN_STARTED, { turn_id: 'turn-1' }),
      piEvent(AgentEventType.CONTENT_STARTED, { turn_id: 'turn-1', content_id: 'c-0', kind: 'reasoning' }),
      piEvent(AgentEventType.CONTENT_DELTA, { turn_id: 'turn-1', content_id: 'c-0', kind: 'reasoning', delta: 'thinking...' }),
      piEvent(AgentEventType.CONTENT_STARTED, { turn_id: 'turn-1', content_id: 'c-1', kind: 'answer' }),
      piEvent(AgentEventType.CONTENT_DELTA, { turn_id: 'turn-1', content_id: 'c-1', kind: 'answer', delta: 'answer' }),
      piEvent(AgentEventType.CONTENT_COMPLETED, { turn_id: 'turn-1', content_id: 'c-0', kind: 'reasoning', text: 'thinking...' }),
    ]
    for (const record of records) processV2Record(state, record)

    expect(state.blocks.map((b) => b.status)).toEqual(['done', 'streaming'])

    processV2Record(state, piEvent(AgentEventType.CONTENT_COMPLETED, {
      turn_id: 'turn-1', content_id: 'c-1', kind: 'answer', text: 'answer'
    }))
    expect(state.blocks.map((b) => b.status)).toEqual(['done', 'done'])
  })

  it('settles every block in a completed turn before the next turn streams', () => {
    const state = feed([
      piEvent(AgentEventType.RUN_STARTED),
      piEvent(AgentEventType.TURN_STARTED, { turn_id: 'turn-1' }),
      piEvent(AgentEventType.CONTENT_DELTA, { content_id: 'c-0', kind: 'answer', delta: 'first' }),
      piEvent(AgentEventType.TURN_COMPLETED, { turn_id: 'turn-1' }),
      piEvent(AgentEventType.TURN_STARTED, { turn_id: 'turn-2' }),
      piEvent(AgentEventType.CONTENT_DELTA, { content_id: 'c-0', kind: 'answer', delta: 'second' }),
    ])

    expect(state.turns[0].status).toBe('done')
    expect(state.turns[0].blocks[0].status).toBe('done')
    expect(state.turns[1].status).toBe('streaming')
    expect(state.turns[1].blocks[0].status).toBe('streaming')
  })

  it('records usage', () => {
    const state = feed([
      piEvent(AgentEventType.USAGE_UPDATED, { usage: { input: 10, output: 5 } }),
    ])
    expect(state.usage).toEqual({ input: 10, output: 5 })
  })

  it('leaves SDK records untouched', () => {
    // The dispatch must not disturb the engine it did not come from.
    const state = feed([
      { record_type: 'stream', data: { type: 'message_start' } },
      { record_type: 'stream', data: { type: 'content_block_start', index: 0, content_block: { type: 'text' } } },
      {
        record_type: 'stream',
        data: { type: 'content_block_delta', index: 0, delta: { type: 'text_delta', text: 'sdk' } },
      },
    ])

    expect(state.blocks.map((b) => b.content)).toEqual(['sdk'])
  })
})

describe('Pi and SDK blocks are structurally interchangeable', () => {
  // The projection contract fixture compares rendered *content*. It does not
  // look at the rendering metadata the chat template depends on, so a Pi block
  // could carry the right text and still break the DOM. These assertions cover
  // that gap.

  function sdkState() {
    return feed([
      { record_type: 'stream', data: { type: 'message_start' } },
      { record_type: 'stream', data: { type: 'content_block_start', index: 0, content_block: { type: 'thinking' } } },
      { record_type: 'stream', data: { type: 'content_block_delta', index: 0, delta: { type: 'thinking_delta', thinking: 't' } } },
      { record_type: 'stream', data: { type: 'content_block_start', index: 1, content_block: { type: 'text' } } },
      { record_type: 'stream', data: { type: 'content_block_delta', index: 1, delta: { type: 'text_delta', text: 'a' } } },
      { record_type: 'stream', data: { type: 'content_block_start', index: 2, content_block: { type: 'tool_use', id: 't1', name: 'run_sql' } } },
    ])
  }

  function piState() {
    return feed([
      piEvent(AgentEventType.RUN_STARTED),
      piEvent(AgentEventType.TURN_STARTED, { turn_id: 'turn-1' }),
      piEvent(AgentEventType.CONTENT_DELTA, { content_id: 'c-0', kind: 'reasoning', delta: 't' }),
      piEvent(AgentEventType.CONTENT_DELTA, { content_id: 'c-1', kind: 'answer', delta: 'a' }),
      piEvent(AgentEventType.TOOL_STARTED, { tool_call_id: 't1', tool_name: 'run_sql' }),
    ])
  }

  it('produces blocks with the same field set', () => {
    const sdk = sdkState()
    const pi = piState()

    expect(pi.blocks.length).toBe(sdk.blocks.length)
    for (let i = 0; i < sdk.blocks.length; i += 1) {
      expect(Object.keys(pi.blocks[i]).sort()).toEqual(Object.keys(sdk.blocks[i]).sort())
      expect(pi.blocks[i].type).toBe(sdk.blocks[i].type)
    }
  })

  it('produces turns with the same field set', () => {
    const sdk = sdkState()
    const pi = piState()

    expect(Object.keys(pi.turns[0]).sort()).toEqual(Object.keys(sdk.turns[0]).sort())
  })

  it('gives every block in a turn a distinct blockIndex', () => {
    // The chat template keys its v-for on `block.blockIndex + '-' + ti`.
    // Repeated keys make Vue reuse the wrong nodes; undefined keys make every
    // block in the turn collide.
    const pi = piState()
    const keys = pi.turns[0].blocks.map((b) => b.blockIndex)

    expect(keys.every((k) => typeof k === 'number')).toBe(true)
    expect(new Set(keys).size).toBe(keys.length)
  })
})
