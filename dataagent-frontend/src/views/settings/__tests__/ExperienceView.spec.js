import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import ElementPlus from 'element-plus'

const apiMocks = vi.hoisted(() => ({
  listAgentProfiles: vi.fn(),
  listExperiences: vi.fn(),
  updateExperience: vi.fn(),
  createExperience: vi.fn()
}))
const push = vi.hoisted(() => vi.fn())

vi.mock('@/api/dataagent', () => ({ dataagentApi: apiMocks }))
vi.mock('vue-router', () => ({ useRouter: () => ({ push }) }))

import ExperienceView from '../ExperienceView.vue'

const listResponse = (items, extra = {}) => ({
  items,
  total: items.length,
  page: 1,
  page_size: 20,
  digest_enabled: true,
  injection_enabled: false,
  digest_agent_id: 'agent_digest',
  ...extra
})

const candidateRow = {
  experience_id: 'exp-1',
  scope: 'assistant',
  agent_id: 'agent_a',
  content: '查询订单表时优先过滤已取消状态',
  source_task_ids: ['task-1', 'task-2'],
  sources: [
    { task_id: 'task-1', topic_id: 'topic-src-1', agent_id: 'agent_a', source: 'web' },
    { task_id: 'task-2', topic_id: 'topic-src-2', agent_id: 'agent_a', source: 'widget' }
  ],
  digest_topic_id: 'topic-digest-1',
  status: 'candidate',
  updated_by: 'system:experience-digest',
  created_at: '2026-11-19T02:00:00',
  updated_at: '2026-11-19T02:05:00'
}

const activeAssistantRow = {
  ...candidateRow,
  experience_id: 'exp-2',
  status: 'active',
  content: '聚合前先去重主键'
}

const activePlatformRow = {
  experience_id: 'exp-3',
  scope: 'platform',
  agent_id: null,
  content: '日期字段统一使用北京时间',
  source_task_ids: [],
  digest_topic_id: null,
  status: 'active',
  updated_by: 'admin',
  created_at: '2026-11-18T10:00:00',
  updated_at: '2026-11-18T10:00:00'
}

describe('ExperienceView', () => {
  beforeEach(() => {
    push.mockReset()
    apiMocks.listAgentProfiles.mockReset()
    apiMocks.listExperiences.mockReset()
    apiMocks.updateExperience.mockReset()
    apiMocks.createExperience.mockReset()

    apiMocks.listAgentProfiles.mockResolvedValue([{ agent_id: 'agent_a', name: '问数助手' }])
    apiMocks.listExperiences.mockResolvedValue(listResponse([candidateRow]))
    apiMocks.updateExperience.mockResolvedValue({ ...candidateRow, status: 'active', scope: 'platform' })
    apiMocks.createExperience.mockResolvedValue(activePlatformRow)
  })

  const mountView = async () => {
    const wrapper = mount(ExperienceView, { global: { plugins: [ElementPlus] }, attachTo: document.body })
    await flushPromises()
    return wrapper
  }

  it('loads candidate experiences by default with status tags and agent names', async () => {
    const wrapper = await mountView()

    expect(apiMocks.listExperiences).toHaveBeenCalledWith({
      status: 'candidate',
      page: 1,
      page_size: 20
    })
    expect(wrapper.get('[data-testid="experience-digest-status"]').text()).toContain('每日总结 已开启')
    expect(wrapper.get('[data-testid="experience-injection-status"]').text()).toContain('注入 未开启')
    expect(wrapper.text()).toContain('查询订单表时优先过滤已取消状态')
    expect(wrapper.text()).toContain('问数助手')
    expect(wrapper.text()).toContain('来源 1')
    wrapper.unmount()
  })

  it('reloads with status=active when switching to the 生效 tab', async () => {
    const wrapper = await mountView()
    apiMocks.listExperiences.mockResolvedValueOnce(listResponse([activeAssistantRow, activePlatformRow]))

    const activeTab = wrapper.find('#tab-active')
    expect(activeTab.exists()).toBe(true)
    await activeTab.trigger('click')
    await flushPromises()

    expect(apiMocks.listExperiences).toHaveBeenLastCalledWith({
      status: 'active',
      page: 1,
      page_size: 20
    })
    expect(wrapper.text()).toContain('全部助手')
    expect(wrapper.find('[data-testid="experience-promote-exp-2"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="experience-promote-exp-3"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('sends PATCH { status: active, scope: platform } when clicking 设为平台级', async () => {
    const wrapper = await mountView()

    await wrapper.get('[data-testid="experience-activate-platform-exp-1"]').trigger('click')
    await flushPromises()

    expect(apiMocks.updateExperience).toHaveBeenCalledWith('exp-1', {
      status: 'active',
      scope: 'platform'
    })
    expect(apiMocks.listExperiences).toHaveBeenCalledTimes(2)
    wrapper.unmount()
  })

  it('navigates to /chat with digest_agent_id and digest_topic_id when clicking 查看总结会话', async () => {
    const wrapper = await mountView()

    await wrapper.get('[data-testid="experience-digest-exp-1"]').trigger('click')

    expect(push).toHaveBeenCalledWith({
      path: '/chat',
      query: { agent_id: 'agent_digest', topic_id: 'topic-digest-1' }
    })
    wrapper.unmount()
  })

  it('requires selecting an assistant when creating an assistant-scope experience', async () => {
    const wrapper = await mountView()

    await wrapper.get('[data-testid="experience-create-btn"]').trigger('click')
    await flushPromises()

    wrapper.vm.$.setupState.createDialog.scope = 'assistant'
    wrapper.vm.$.setupState.createDialog.content = '需要指定助手才能生效'
    await flushPromises()

    await wrapper.get('[data-testid="experience-create-submit"]').trigger('click')
    await flushPromises()

    expect(apiMocks.createExperience).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('请选择助手')

    wrapper.vm.$.setupState.createDialog.agentId = 'agent_a'
    await flushPromises()

    await wrapper.get('[data-testid="experience-create-submit"]').trigger('click')
    await flushPromises()

    expect(apiMocks.createExperience).toHaveBeenCalledWith({
      scope: 'assistant',
      agent_id: 'agent_a',
      content: '需要指定助手才能生效'
    })
    wrapper.unmount()
  })

  it('opens a main-site source conversation and only names a Widget one', async () => {
    apiMocks.listAgentProfiles.mockResolvedValue([{ agent_id: 'agent_a', name: '问数助手' }])
    apiMocks.listExperiences.mockResolvedValue(listResponse([candidateRow]))
    const wrapper = mount(ExperienceView, { global: { plugins: [ElementPlus] }, attachTo: document.body })
    await flushPromises()
    expect(wrapper.find('[data-testid="experience-source-task-2"]').exists()).toBe(false)
    expect(wrapper.text()).toContain('Widget 来源 2')
    await wrapper.get('[data-testid="experience-source-task-1"]').trigger('click')
    expect(push).toHaveBeenCalledWith({ path: '/chat', query: { agent_id: 'agent_a', topic_id: 'topic-src-1' } })
    wrapper.unmount()
  })
})
