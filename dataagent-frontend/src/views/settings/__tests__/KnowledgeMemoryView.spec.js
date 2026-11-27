import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import ElementPlus, { ElMessageBox } from 'element-plus'

const api = vi.hoisted(() => Object.fromEntries(
  ['listAgentProfiles', 'listExperiences', 'updateExperience', 'createExperience', 'deleteExperience'].map(k => [k, vi.fn()])
))
const push = vi.hoisted(() => vi.fn())
const routeQuery = vi.hoisted(() => ({ value: {} }))

vi.mock('@/api/dataagent', () => ({ dataagentApi: api }))
vi.mock('vue-router', () => ({
  useRoute: () => ({ query: routeQuery.value }),
  useRouter: () => ({ push })
}))

import KnowledgeMemoryView from '../KnowledgeMemoryView.vue'
import ExperienceDrawer from '@/components/experience/ExperienceDrawer.vue'

const digestCandidate = {
  experience_id: 'e1',
  title: '确认关联键',
  when_text: '汇总前',
  body: '先去重再汇总',
  scope: 'agent',
  agent_id: 'a',
  source: 'digest',
  status: 'pending',
  digest_topic_id: 'digest',
  updated_at: '2026-11-19T00:00:00Z',
  sources: [
    { task_id: 't1', source: 'web', agent_id: 'a', topic_id: 'source-topic', question: '销售趋势' },
    { task_id: 't2', source: 'widget', agent_id: 'a', topic_id: 'widget-source', question: '退款率', answer: '来源摘要' }
  ]
}

const manualKnowledge = {
  experience_id: 'e2',
  title: '销售额口径',
  when_text: '统计销售额时',
  body: '销售额不含已退款订单。',
  scope: 'global',
  agent_id: null,
  source: 'manual',
  status: 'active',
  digest_topic_id: null,
  updated_at: '2026-11-20T00:00:00Z',
  sources: []
}

const response = items => ({
  items,
  total: items.length,
  digest_agent_id: 'digest-agent',
  digest_enabled: true,
  injection_enabled: false
})

let wrappers = []
const mountPage = async (query = {}) => {
  routeQuery.value = query
  const wrapper = mount(KnowledgeMemoryView, { global: { plugins: [ElementPlus] } })
  wrappers.push(wrapper)
  await flushPromises()
  return wrapper
}

beforeEach(() => {
  wrappers.forEach(w => w.unmount())
  wrappers = []
  document.body.innerHTML = ''
  vi.clearAllMocks()
  routeQuery.value = {}
  api.listAgentProfiles.mockResolvedValue([{ agent_id: 'a', name: '销售助手' }])
  api.listExperiences.mockImplementation(params => Promise.resolve(
    response(params.status === 'pending' ? [digestCandidate] : [manualKnowledge, { ...digestCandidate, status: 'active' }])
  ))
  api.createExperience.mockResolvedValue({})
  api.updateExperience.mockResolvedValue({})
  api.deleteExperience.mockResolvedValue({})
})

describe('Knowledge management page', () => {
  it('renders a single knowledge list with search placeholder, source column, and large editor up to 20000 chars', async () => {
    const wrapper = await mountPage()
    expect(wrapper.text()).toContain('新增知识')
    expect(wrapper.text()).not.toContain('记忆')
    expect(wrapper.find('input[placeholder="搜索标题、适用场景或正文"]').exists()).toBe(true)
    expect(wrapper.text()).toContain('管理员添加')
    expect(wrapper.text()).toContain('每日总结')
    expect(api.listExperiences).toHaveBeenCalledWith(expect.not.objectContaining({ kind: expect.anything() }))

    wrapper.vm.openDrawer(null, true)
    await flushPromises()
    const drawer = wrapper.findComponent(ExperienceDrawer)
    const textarea = drawer.find('textarea')
    expect(textarea.exists()).toBe(true)
    expect(Number(textarea.attributes('maxlength'))).toBe(20000)
    expect(textarea.attributes('placeholder')).not.toContain('不要写业务口径定义')

    const longBody = '口'.repeat(3000)
    drawer.vm.form = { title: '长口径文档', when_text: '查销售额时', body: longBody, target: 'global' }
    await flushPromises()
    await drawer.vm.save()
    expect(api.createExperience).toHaveBeenCalledWith({
      title: '长口径文档',
      when_text: '查销售额时',
      body: longBody,
      scope: 'global',
      agent_id: null
    })
  })

  it('rejects manual body over 20000 chars and digest body over 500 chars', async () => {
    const wrapper = await mountPage()
    wrapper.vm.openDrawer(null, true)
    await flushPromises()
    const drawer = wrapper.findComponent(ExperienceDrawer)
    drawer.vm.form = { title: '超长', when_text: '场景', body: '长'.repeat(20001), target: 'global' }
    await flushPromises()
    await drawer.vm.save()
    expect(api.createExperience).not.toHaveBeenCalled()
    await flushPromises()
    await vi.waitFor(() => expect(drawer.text()).toContain('最多 20000 字'))

    const pendingWrapper = await mountPage({ status: 'pending' })
    pendingWrapper.vm.reviewForm.body = '长'.repeat(501)
    await flushPromises()
    await pendingWrapper.vm.approve()
    expect(api.updateExperience).not.toHaveBeenCalled()
    await flushPromises()
    await vi.waitFor(() => expect(pendingWrapper.text()).toContain('最多 500 字'))
  })

  it('reviews pending digest knowledge: editable in place, approvable, ignorable, and shows sources only for digest', async () => {
    const wrapper = await mountPage({ status: 'pending' })
    expect(wrapper.find('.review-queue').text()).toContain('确认关联键')
    expect(wrapper.vm.reviewForm.target).toBe('a')

    // Sources block renders for source === 'digest'
    const links = wrapper.findAll('.entry-sources .el-link')
    expect(links).toHaveLength(3)
    await links[0].trigger('click')
    expect(push).toHaveBeenLastCalledWith({ path: '/chat', query: { agent_id: 'digest-agent', topic_id: 'digest' } })
    await links[1].trigger('click')
    expect(push).toHaveBeenLastCalledWith({ path: '/chat', query: { agent_id: 'a', topic_id: 'source-topic' } })
    await links[2].trigger('click')
    expect(push).toHaveBeenLastCalledWith({ path: '/chat', query: { agent_id: 'a', topic_id: 'widget-source' } })
    expect(wrapper.find('.entry-sources').text()).toContain('来源摘要')

    // Save edits while staying pending
    wrapper.vm.reviewForm = { title: '修订草稿', when_text: '修订场景', body: '修订正文', target: 'a' }
    await flushPromises()
    await wrapper.vm.savePending()
    expect(api.updateExperience).toHaveBeenCalledWith('e1', {
      title: '修订草稿',
      when_text: '修订场景',
      body: '修订正文',
      scope: 'agent',
      agent_id: 'a'
    })

    // Approve into active
    wrapper.vm.reviewForm = { title: '修订标题', when_text: '修订条件', body: '修订内容', target: 'global' }
    await flushPromises()
    await wrapper.vm.approve()
    expect(api.updateExperience).toHaveBeenLastCalledWith('e1', {
      title: '修订标题',
      when_text: '修订条件',
      body: '修订内容',
      scope: 'global',
      agent_id: null,
      status: 'active'
    })

    // Ignore -> disabled, restore -> active
    await wrapper.vm.changeStatus(digestCandidate, 'disabled')
    expect(api.updateExperience).toHaveBeenLastCalledWith('e1', { status: 'disabled' })
    await wrapper.vm.changeStatus({ ...digestCandidate, status: 'disabled' }, 'active')
    expect(api.updateExperience).toHaveBeenLastCalledWith('e1', { status: 'active' })
  })

  it('hides ExperienceSources for manual entries and shows it for digest entries in drawer', async () => {
    const wrapper = await mountPage()
    const drawer = wrapper.findComponent(ExperienceDrawer)
    wrapper.vm.openDrawer(manualKnowledge, false)
    await flushPromises()
    expect(drawer.find('.entry-sources').exists()).toBe(false)

    wrapper.vm.openDrawer({ ...digestCandidate, status: 'active' }, false)
    await flushPromises()
    expect(drawer.find('.entry-sources').exists()).toBe(true)
  })

  it('promotes and toggles via row menu without injection mode', async () => {
    const wrapper = await mountPage()
    const row = { ...digestCandidate, status: 'active' }
    await wrapper.vm.handleCommand('promote', row)
    expect(api.updateExperience).toHaveBeenLastCalledWith('e1', { scope: 'global', agent_id: null, status: 'active' })
    await wrapper.vm.handleCommand('toggle', row)
    expect(api.updateExperience).toHaveBeenLastCalledWith('e1', { status: 'disabled' })
  })

  it('hard deletes after confirmation', async () => {
    vi.spyOn(ElMessageBox, 'confirm').mockResolvedValueOnce('confirm')
    const wrapper = await mountPage({ status: 'pending' })
    await wrapper.vm.remove(digestCandidate)
    expect(api.deleteExperience).toHaveBeenCalledWith('e1')
  })

  it('shows a retryable loading error without an empty success state', async () => {
    api.listExperiences.mockRejectedValue(new Error('连接失败'))
    const wrapper = await mountPage({ status: 'pending' })
    expect(wrapper.find('.el-alert').text()).toContain('连接失败')
    expect(wrapper.find('.el-empty').exists()).toBe(false)
  })
})
