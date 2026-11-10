import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { nextTick, reactive } from 'vue'

import DataAgentWidget from '../DataAgentWidget.vue'

const createObjectURLDescriptor = Object.getOwnPropertyDescriptor(URL, 'createObjectURL')
const revokeObjectURLDescriptor = Object.getOwnPropertyDescriptor(URL, 'revokeObjectURL')

// ECharts touches the canvas API, which jsdom doesn't fully implement. The chart
// promotion tests only care about which container the chart lands in, so stub the
// renderer to keep the conclusion-area direct chart from emitting canvas errors.
const echartsMocks = vi.hoisted(() => {
  const instance = { setOption: vi.fn(), resize: vi.fn(), clear: vi.fn(), dispose: vi.fn() }
  return { instance, init: vi.fn(() => instance) }
})

vi.mock('echarts/core', () => ({
  use: () => {},
  init: echartsMocks.init
}))
vi.mock('echarts/charts', () => ({
  BarChart: {},
  LineChart: {},
  PieChart: {},
  ScatterChart: {},
  RadarChart: {},
  FunnelChart: {},
  GaugeChart: {}
}))
vi.mock('echarts/components', () => ({
  GridComponent: {},
  LegendComponent: {},
  TitleComponent: {},
  TooltipComponent: {},
  RadarComponent: {}
}))
vi.mock('echarts/renderers', () => ({ CanvasRenderer: {} }))

const apiMocks = vi.hoisted(() => ({
  createClient: vi.fn(),
  runtimeApi: {
    getConfig: vi.fn()
  },
  topicApi: {
    createTopic: vi.fn(),
    listTopics: vi.fn(),
    getTopicMessages: vi.fn(),
    deleteTopic: vi.fn(),
    updateTopic: vi.fn(),
    updateMessageFeedback: vi.fn(),
    fetchFileBlob: vi.fn()
  },
  taskApi: {
    deliverMessage: vi.fn(),
    streamSdkEvents: vi.fn(),
    getTask: vi.fn(),
    cancelTask: vi.fn(),
    submitQuestionAnswer: vi.fn()
  }
}))

vi.mock('@/api/nl2sql', () => ({
  createNl2SqlApiClient: apiMocks.createClient
}))

const topic = (topicId, title, extra = {}) => ({
  topic_id: topicId,
  title,
  message_count: 2,
  last_message_preview: `${title} preview`,
  updated_at: '2026-04-30T08:00:00',
  ...extra
})

function mountWidget() {
  const state = reactive({
    instanceId: 'w1', topicId: null, panelMode: 'floating', isOpen: false, historyOpen: false,
    loginRequired: false, isBusy: false, outboundMessage: '', requestedDraft: '', draftSignal: 0,
    cancelSignal: 0, newConversationSignal: 0, selectConversationSignal: 0, deleteConversationSignal: 0,
    requestedTopicId: '', deleteRequestedTopicId: '', track: () => {},
    unreadNoticeCount: 0, workingCount: 0, activeLauncherNotice: null
  })
  const config = {
    displayMode: 'floating', projectName: '智能问数', projectColor: '#4A90A4', agentId: 'agent_widget',
    headers: { 'X-DataAgent-Client': 'widget', 'X-DataAgent-Website-Id': 'demo', 'X-DataAgent-User-Id': 'u1' }
  }
  const wrapper = mount(DataAgentWidget, { props: { config, state }, attachTo: document.body })
  return { wrapper, state }
}

describe('DataAgentWidget background tasks while minimized', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    apiMocks.createClient.mockReturnValue(apiMocks)
    apiMocks.runtimeApi.getConfig.mockResolvedValue({ default_provider_id: 'p', default_model: 'm', providers: [] })
    apiMocks.topicApi.getTopicMessages.mockResolvedValue({ topic_id: 'topic-bg', total: 0, items: [] })
    apiMocks.taskApi.getTask.mockResolvedValue({ task_status: 'finished' })
    apiMocks.taskApi.streamSdkEvents.mockResolvedValue()
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  it('loads nothing until the first open', async () => {
    const { wrapper } = mountWidget()
    await flushPromises()
    expect(apiMocks.topicApi.listTopics).not.toHaveBeenCalled()
    expect(wrapper.find('.odw-panel').exists()).toBe(false)
    wrapper.unmount()
  })

  it('keeps tracking after minimize and shows the launcher notice that opens the finished topic', async () => {
    vi.useFakeTimers()
    const running = topic('topic-bg', '后台运行会话', { current_task_id: 'task-bg', current_task_status: 'running' })
    const finished = topic('topic-bg', '后台运行会话', { current_task_id: 'task-bg', current_task_status: 'finished' })
    let current = running
    apiMocks.topicApi.listTopics.mockImplementation(async () => [current])

    const { wrapper, state } = mountWidget()
    state.isOpen = true
    await flushPromises()
    expect(state.workingCount).toBe(1)

    // Minimize: the chat stays mounted (hidden) so polling continues.
    state.isOpen = false
    await flushPromises()
    expect(wrapper.find('.odw-panel').exists()).toBe(true)
    expect(wrapper.find('.odw-panel').isVisible()).toBe(false)
    expect(wrapper.find('.odw-launcher__working').exists()).toBe(true)

    current = finished
    await vi.advanceTimersByTimeAsync(3000)
    await flushPromises()

    expect(state.workingCount).toBe(0)
    expect(state.unreadNoticeCount).toBe(1)
    expect(wrapper.find('.odw-launcher__badge').exists()).toBe(true)
    expect(wrapper.find('.odw-launcher__working').exists()).toBe(false)
    const bubble = wrapper.find('.odw-launcher-bubble')
    expect(bubble.exists()).toBe(true)
    expect(bubble.text()).toContain('后台运行会话')

    await bubble.trigger('click')
    await flushPromises()
    expect(state.isOpen).toBe(true)
    expect(state.topicId).toBe('topic-bg')
    expect(state.unreadNoticeCount).toBe(0)
    wrapper.unmount()
  })
})
