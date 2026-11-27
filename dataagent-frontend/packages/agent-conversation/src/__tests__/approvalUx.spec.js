import { describe, expect, it, vi } from 'vitest'
import { mount, shallowMount } from '@vue/test-utils'
import { createChatState, processV2Record, blockToToolProp } from '../core/streamParser.js'
import { buildV2StateFromStoredRecords } from '../core/message.js'
import ToolOutput from '../ui/ToolOutput.vue'
import ConversationRoot from '../ui/ConversationRoot.vue'

describe('approval UX single-row tool lifecycle', () => {
  it('shows a call skipped while another call waits as 未执行, not 执行失败', () => {
    const state = createChatState()
    processV2Record(state, { record_type: 'agent_event', event_type: 'turn.started', data: { turn_id: 'turn-1' } })
    processV2Record(state, {
      record_type: 'agent_event',
      event_type: 'tool.started',
      data: { turn_id: 'turn-1', tool_call_id: 'call-2', tool_name: 'create_table', input: { name: 'b' } },
    })
    processV2Record(state, {
      record_type: 'agent_event',
      event_type: 'tool.completed',
      data: { turn_id: 'turn-1', tool_call_id: 'call-2', tool_name: 'create_table', output: null, is_error: false, not_executed: true },
    })
    const [block] = state.blocks.filter((b) => b.type === 'tool_use')
    expect(blockToToolProp(block).status).toBe('not_executed')
  })

  it('merges awaiting -> allow -> executing -> executed into one tool row', () => {
    const state = createChatState()
    processV2Record(state, { record_type: 'agent_event', event_type: 'turn.started', data: { turn_id: 'turn-1' } })
    processV2Record(state, {
      record_type: 'agent_event',
      event_type: 'tool.started',
      data: { turn_id: 'turn-1', tool_call_id: 'call-1', tool_name: 'create_table', tool_title: '创建表', input: { name: 't1' } },
    })
    processV2Record(state, {
      record_type: 'agent_event',
      event_type: 'tool.completed',
      data: { turn_id: 'turn-1', tool_call_id: 'call-1', tool_name: 'create_table', output: null, is_error: false, awaiting_approval: true },
    })
    processV2Record(state, {
      record_type: 'permission_request',
      data: { request_id: 'run1:call-1', tool_call_id: 'call-1', tool_name: 'create_table', tool_title: '创建表', destructive: false },
    })

    let toolBlocks = state.blocks.filter((b) => b.type === 'tool_use')
    expect(toolBlocks).toHaveLength(1)
    expect(blockToToolProp(toolBlocks[0]).status).toBe('awaiting_approval')

    processV2Record(state, {
      record_type: 'permission_decision',
      data: { request_id: 'run1:call-1', decision: 'allow', note: '', remember: 'session' },
    })
    expect(blockToToolProp(toolBlocks[0]).status).toBe('awaiting_approval')

    processV2Record(state, {
      record_type: 'agent_event',
      event_type: 'tool.started',
      data: { turn_id: 'turn-1', tool_call_id: 'call-1', tool_name: 'create_table', tool_title: '创建表', input: { name: 't1' } },
    })
    toolBlocks = state.blocks.filter((b) => b.type === 'tool_use')
    expect(toolBlocks).toHaveLength(1)
    expect(blockToToolProp(toolBlocks[0]).status).toBe('streaming')

    processV2Record(state, {
      record_type: 'agent_event',
      event_type: 'tool.completed',
      data: { turn_id: 'turn-1', tool_call_id: 'call-1', tool_name: 'create_table', output: [{ type: 'text', text: 'ok' }], is_error: false },
    })
    expect(toolBlocks).toHaveLength(1)
    expect(blockToToolProp(toolBlocks[0]).status).toBe('success')
  })

  it('marks denied and cancelled/timeout calls with muted statuses', () => {
    const deniedState = createChatState()
    processV2Record(deniedState, { record_type: 'agent_event', event_type: 'turn.started', data: { turn_id: 'turn-1' } })
    processV2Record(deniedState, {
      record_type: 'agent_event',
      event_type: 'tool.started',
      data: { turn_id: 'turn-1', tool_call_id: 'call-d', tool_name: 'drop_table', tool_title: '删除表', input: {} },
    })
    processV2Record(deniedState, {
      record_type: 'agent_event',
      event_type: 'tool.completed',
      data: { turn_id: 'turn-1', tool_call_id: 'call-d', tool_name: 'drop_table', output: null, is_error: false, awaiting_approval: true },
    })
    processV2Record(deniedState, {
      record_type: 'permission_request',
      data: { request_id: 'run1:call-d', tool_call_id: 'call-d', tool_name: 'drop_table', destructive: true },
    })
    processV2Record(deniedState, {
      record_type: 'permission_decision',
      data: { request_id: 'run1:call-d', decision: 'deny', note: '不要删' },
    })
    expect(blockToToolProp(deniedState.blocks[0]).status).toBe('denied')

    const cancelledState = createChatState()
    processV2Record(cancelledState, { record_type: 'agent_event', event_type: 'turn.started', data: { turn_id: 'turn-1' } })
    processV2Record(cancelledState, {
      record_type: 'agent_event',
      event_type: 'tool.started',
      data: { turn_id: 'turn-1', tool_call_id: 'call-c', tool_name: 'drop_table', input: {} },
    })
    processV2Record(cancelledState, {
      record_type: 'agent_event',
      event_type: 'tool.completed',
      data: { turn_id: 'turn-1', tool_call_id: 'call-c', tool_name: 'drop_table', output: null, is_error: false, awaiting_approval: true },
    })
    processV2Record(cancelledState, { record_type: 'agent_event', event_type: 'run.cancelled', data: { reason: 'user' } })
    expect(blockToToolProp(cancelledState.blocks[0]).status).toBe('not_executed')
  })

  it('keeps awaiting_approval on active reload and marks not_executed on terminal reload', () => {
    const records = [
      { record_type: 'agent_event', event_type: 'turn.started', data: { turn_id: 'turn-1' } },
      { record_type: 'agent_event', event_type: 'tool.started', data: { turn_id: 'turn-1', tool_call_id: 'c1', tool_name: 'create_table', input: {} } },
      { record_type: 'agent_event', event_type: 'tool.completed', data: { turn_id: 'turn-1', tool_call_id: 'c1', tool_name: 'create_table', output: null, is_error: false, awaiting_approval: true } },
    ]
    const waitingState = buildV2StateFromStoredRecords({ status: 'waiting_permission', records })
    expect(blockToToolProp(waitingState.blocks[0]).status).toBe('awaiting_approval')

    const suspendedState = buildV2StateFromStoredRecords({ status: 'suspended', records })
    expect(blockToToolProp(suspendedState.blocks[0]).status).toBe('not_executed')
  })

  it('renders ToolOutput status text and tone classes for all approval states', () => {
    const cases = [
      { tool: { name: 'create_table', tool_title: '创建表', status: 'awaiting_approval', _callComplete: true, _runtimeStarted: false }, text: '等待确认', tone: 'is-awaiting' },
      { tool: { name: 'create_table', tool_title: '创建表', status: 'streaming', _callComplete: false, _runtimeStarted: false }, text: '正在执行', tone: 'is-running' },
      { tool: { name: 'create_table', tool_title: '创建表', status: 'success', output: 'ok', _callComplete: true, _runtimeStarted: true }, text: '已执行', tone: 'is-success' },
      { tool: { name: 'create_table', tool_title: '创建表', status: 'failed', output: 'err', _callComplete: true, _runtimeStarted: true }, text: '执行失败', tone: 'is-failed' },
      { tool: { name: 'drop_table', tool_title: '删除表', status: 'denied', _callComplete: true, _runtimeStarted: false }, text: '已拒绝', tone: 'is-muted' },
      { tool: { name: 'drop_table', tool_title: '删除表', status: 'not_executed', _callComplete: true, _runtimeStarted: false }, text: '未执行', tone: 'is-muted' },
    ]
    for (const c of cases) {
      const wrapper = shallowMount(ToolOutput, { props: { tool: c.tool } })
      const status = wrapper.get('.shell-trace-summary-status')
      expect(status.text()).toBe(c.text)
      expect(status.classes()).toContain(c.tone)
    }
  })

  it('ConversationRoot forwards remember and note to transport.submitInteraction', async () => {
    const submitInteraction = vi.fn(async () => {})
    const transport = {
      loadConversation: vi.fn(async () => ({
        messages: [{
          id: 'm1',
          role: 'assistant',
          taskId: 'task-1',
          status: 'waiting_permission',
          records: [
            { record_type: 'agent_event', event_type: 'turn.started', data: { turn_id: 'turn-1' } },
            {
              record_type: 'permission_request',
              data: { request_id: 'req-1', tool_name: 'create_table', tool_title: '创建表', destructive: false },
            },
          ],
        }],
        run: { taskId: 'task-1', status: 'waiting_permission', detail: '' },
      })),
      sendMessage: vi.fn(),
      streamEvents: vi.fn(async function* () {}),
      cancelRun: vi.fn(),
      submitInteraction,
      fileUrl: (p) => p,
    }
    const wrapper = mount(ConversationRoot, {
      props: { endpoint: '/conv/1', transportFactory: () => transport },
    })
    await new Promise((r) => setTimeout(r, 20))
    const sessionBtn = wrapper.find('.v2-perm-btn.allow-session')
    expect(sessionBtn.exists()).toBe(true)
    await sessionBtn.trigger('click')
    expect(submitInteraction).toHaveBeenCalledWith({
      taskId: 'task-1',
      kind: 'permission',
      requestId: 'req-1',
      payload: { decision: 'allow', remember: 'session' },
    })
    wrapper.unmount()
  })
})
