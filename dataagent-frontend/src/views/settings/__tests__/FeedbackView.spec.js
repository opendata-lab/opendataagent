import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import ElementPlus from 'element-plus'

const apiMocks = vi.hoisted(() => ({
  listAgentProfiles: vi.fn(),
  listFeedback: vi.fn()
}))
const push = vi.hoisted(() => vi.fn())

vi.mock('@/api/dataagent', () => ({ dataagentApi: apiMocks }))
vi.mock('vue-router', () => ({ useRouter: () => ({ push }) }))

import FeedbackView from '../FeedbackView.vue'

const page = (items, extra = {}) => ({ items, total: items.length, page: 1, page_size: 20, likes: 1, dislikes: 1, ...extra })

const webRow = {
  message_id: 'msg-1', topic_id: 'topic-1', agent_id: 'agent_a', feedback: 'dislike',
  feedback_at: '2026-11-19T08:00:00', question: '各渠道退款率', answer: '退款率如下', source: 'web'
}
const widgetRow = { ...webRow, message_id: 'msg-2', topic_id: 'topic-2', feedback: 'like', source: 'widget' }

describe('FeedbackView', () => {
  beforeEach(() => {
    push.mockReset()
    apiMocks.listAgentProfiles.mockResolvedValue([{ agent_id: 'agent_a', name: '问数助手' }])
    apiMocks.listFeedback.mockResolvedValue(page([webRow, widgetRow]))
  })

  const mountView = async () => {
    const wrapper = mount(FeedbackView, { global: { plugins: [ElementPlus] }, attachTo: document.body })
    await flushPromises()
    return wrapper
  }

  it('lists rated answers with counts and agent names', async () => {
    const wrapper = await mountView()
    expect(apiMocks.listFeedback).toHaveBeenCalledWith({ page: 1, page_size: 20 })
    expect(wrapper.get('[data-testid="feedback-counts"]').text()).toContain('点赞 1')
    expect(wrapper.text()).toContain('各渠道退款率')
    expect(wrapper.text()).toContain('问数助手')
    wrapper.unmount()
  })

  it('opens a main-site conversation by agent, topic and message; widget rows have no link', async () => {
    const wrapper = await mountView()
    expect(wrapper.find('[data-testid="feedback-open-msg-2"]').exists()).toBe(false)
    await wrapper.get('[data-testid="feedback-open-msg-1"]').trigger('click')
    expect(push).toHaveBeenCalledWith({ path: '/chat', query: { agent_id: 'agent_a', topic_id: 'topic-1', message_id: 'msg-1' } })
    wrapper.unmount()
  })

  it('passes the rating and date filters to the API', async () => {
    const wrapper = await mountView()
    wrapper.vm.$.setupState.filters.feedback = 'dislike'
    wrapper.vm.$.setupState.filters.range = ['2026-11-01', '2026-11-19']
    await flushPromises()
    expect(apiMocks.listFeedback).toHaveBeenLastCalledWith({
      page: 1, page_size: 20, feedback: 'dislike', start: '2026-11-01 00:00:00', end: '2026-11-19 23:59:59'
    })
    wrapper.unmount()
  })
})
