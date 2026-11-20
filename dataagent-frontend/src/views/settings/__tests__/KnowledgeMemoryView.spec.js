import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import ElementPlus, { ElMessageBox } from 'element-plus'
const api = vi.hoisted(() => Object.fromEntries(['listAgentProfiles', 'listExperiences', 'updateExperience', 'createExperience', 'deleteExperience'].map(k => [k, vi.fn()])))
const push = vi.hoisted(() => vi.fn())
vi.mock('@/api/dataagent', () => ({ dataagentApi: api }))
vi.mock('vue-router', () => ({ useRoute: () => ({ query: {} }), useRouter: () => ({ push }) }))
import KnowledgeMemoryView from '../KnowledgeMemoryView.vue'
import ExperienceDrawer from '@/components/experience/ExperienceDrawer.vue'
const candidate = { experience_id: 'e1', kind: 'memory', title: '确认关联键', when_text: '汇总前', body: '先去重再汇总', scope: 'agent', agent_id: 'a', inject_mode: 'on_demand', status: 'pending',
  digest_topic_id: 'digest', updated_at: '2026-11-19', sources: [
    { task_id: 't1', source: 'web', agent_id: 'a', topic_id: 'source-topic', question: '销售趋势' },
    { task_id: 't2', source: 'widget', question: '退款率', answer: '来源摘要' }
  ] }
const response = items => ({ items, total: items.length, digest_agent_id: 'digest-agent', digest_enabled: true, injection_enabled: false })
let wrappers = []
const create = async kind => {
  const wrapper = mount(KnowledgeMemoryView, { props: { kind }, global: { plugins: [ElementPlus] } })
  wrappers.push(wrapper)
  await flushPromises()
  return wrapper
}
beforeEach(() => {
  wrappers.forEach(w => w.unmount()); wrappers = []
  document.body.innerHTML = ''
  vi.clearAllMocks()
  api.listAgentProfiles.mockResolvedValue([{ agent_id: 'a', name: '销售助手' }])
  api.listExperiences.mockImplementation(params => Promise.resolve(response(params.kind === 'memory' ? [candidate] : [{ ...candidate, kind: 'knowledge', status: 'active' }])))
  api.createExperience.mockResolvedValue({})
  api.updateExperience.mockResolvedValue({})
  api.deleteExperience.mockResolvedValue({})
})
describe('Knowledge and memory pages', () => {
  it('uses quiet title/condition rows, a create button for knowledge, and an edit drawer', async () => {
    const wrapper = await create('knowledge')
    expect(wrapper.text()).toContain('新增知识')
    expect(wrapper.find('.entry-title').text()).toBe(candidate.title)
    expect(wrapper.find('.entry-condition').text()).toBe(candidate.when_text)
    expect(wrapper.findAll('.el-tag')).toHaveLength(0)
    wrapper.vm.openDrawer(null, true)
    await flushPromises()
    const drawer = wrapper.findComponent(ExperienceDrawer)
    drawer.vm.form = { title: '规则', when_text: '查询前', body: '先看表结构', target: 'global', inject_mode: 'always' }
    await flushPromises()
    await drawer.vm.save()
    expect(api.createExperience).toHaveBeenCalledWith({ title: '规则', when_text: '查询前', body: '先看表结构', scope: 'global', agent_id: null, inject_mode: 'always', kind: 'knowledge' })
  })
  it('validates required fields before creating knowledge', async () => {
    const wrapper = await create('knowledge')
    wrapper.vm.openDrawer(null, true)
    await flushPromises()
    await wrapper.findComponent(ExperienceDrawer).vm.save()
    expect(api.createExperience).not.toHaveBeenCalled()
    await flushPromises()
    await vi.waitFor(() => expect(wrapper.findComponent(ExperienceDrawer).text()).toContain('请填写此项'))
  })
  it('reviews memory with editable fields and defaults inherited from the digest', async () => {
    const wrapper = await create('memory')
    expect(wrapper.text()).not.toContain('新增知识')
    expect(wrapper.find('.review-queue').text()).toContain('确认关联键')
    expect(wrapper.vm.reviewForm.target).toBe('a')
    expect(wrapper.vm.reviewForm.inject_mode).toBe('on_demand')
    wrapper.vm.reviewForm = { title: '修订标题', when_text: '修订条件', body: '修订内容', target: 'global', inject_mode: 'always' }
    await flushPromises()
    await wrapper.vm.approve()
    expect(api.updateExperience).toHaveBeenCalledWith('e1', { title: '修订标题', when_text: '修订条件', body: '修订内容', scope: 'global', agent_id: null, inject_mode: 'always', status: 'active' })
  })
  it('ignores into disabled and restores directly to active', async () => {
    const wrapper = await create('memory')
    await wrapper.vm.changeStatus(candidate, 'disabled')
    expect(api.updateExperience).toHaveBeenCalledWith('e1', { status: 'disabled' })
    await wrapper.vm.changeStatus({ ...candidate, status: 'disabled' }, 'active')
    expect(api.updateExperience).toHaveBeenLastCalledWith('e1', { status: 'active' })
  })
  it('opens digest and web sources with their agent context, while widget stays text', async () => {
    const wrapper = await create('memory')
    const links = wrapper.findAll('.entry-sources .el-link')
    expect(links).toHaveLength(2)
    await links[0].trigger('click')
    expect(push).toHaveBeenLastCalledWith({ path: '/chat', query: { agent_id: 'digest-agent', topic_id: 'digest' } })
    await links[1].trigger('click')
    expect(push).toHaveBeenLastCalledWith({ path: '/chat', query: { agent_id: 'a', topic_id: 'source-topic' } })
    expect(wrapper.find('.entry-sources').text()).toContain('Widget 会话')
    expect(wrapper.find('.entry-sources').text()).toContain('来源摘要')
  })
  it('switches injection mode and promotes through the row menu', async () => {
    const wrapper = await create('knowledge')
    const row = { ...candidate, kind: 'knowledge', status: 'active' }
    await wrapper.vm.handleCommand('inject', row)
    expect(api.updateExperience).toHaveBeenLastCalledWith('e1', { inject_mode: 'always', status: 'active' })
    await wrapper.vm.handleCommand('promote', row)
    expect(api.updateExperience).toHaveBeenLastCalledWith('e1', { scope: 'global', agent_id: null, status: 'active' })
  })
  it('hard deletes after confirmation', async () => {
    vi.spyOn(ElMessageBox, 'confirm').mockResolvedValueOnce('confirm')
    const wrapper = await create('memory')
    await wrapper.vm.remove(candidate)
    expect(api.deleteExperience).toHaveBeenCalledWith('e1')
  })
  it('shows a retryable loading error without an empty success state', async () => {
    api.listExperiences.mockRejectedValue(new Error('连接失败'))
    const wrapper = await create('memory')
    expect(wrapper.find('.el-alert').text()).toContain('连接失败')
    expect(wrapper.find('.el-empty').exists()).toBe(false)
  })
})
