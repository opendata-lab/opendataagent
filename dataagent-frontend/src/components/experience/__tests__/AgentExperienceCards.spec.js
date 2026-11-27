import { flushPromises, mount } from '@vue/test-utils'
import { describe, it, expect, vi } from 'vitest'
import ElementPlus from 'element-plus'

const getPreview = vi.hoisted(() => vi.fn())
const push = vi.hoisted(() => vi.fn())

vi.mock('@/api/dataagent', () => ({ dataagentApi: { getExperienceInjectionPreview: getPreview } }))
vi.mock('vue-router', () => ({ useRouter: () => ({ push }) }))

import AgentExperienceCards from '../AgentExperienceCards.vue'
import ExperienceDrawer from '../ExperienceDrawer.vue'

it('shows knowledge entries without budget or injection mode, with eligibility notice and management links', async () => {
  const item = {
    experience_id: 'e1',
    title: '全局口径',
    when_text: '查销售额时',
    body: '不含退款',
    scope: 'global',
    source: 'manual',
    status: 'active'
  }
  getPreview.mockResolvedValue({
    items: [item],
    indexed_ids: [],
    total_count: 1,
    omitted_count: 0,
    pending_count: 2,
    eligible: false,
    reason: 'disabled_by_config',
    injection_enabled: false,
    prompt: '',
    digest_agent_id: 'digest'
  })
  const wrapper = mount(AgentExperienceCards, { props: { agentId: 'a', agentName: '销售助手' }, global: { plugins: [ElementPlus] } })
  await flushPromises()

  expect(wrapper.text()).toContain('知识 · 1 条')
  expect(wrapper.text()).toContain('2 条待审核')
  expect(wrapper.text()).toContain('部署配置未开启注入')
  expect(wrapper.text()).not.toContain('额度')
  expect(wrapper.text()).not.toContain('始终注入')
  expect(wrapper.text()).not.toContain('记忆')

  await wrapper.find('li button').trigger('click')
  const drawer = wrapper.findComponent(ExperienceDrawer)
  expect(drawer.props('editing')).toBe(false)
  expect(drawer.props('item')).toEqual(item)

  await wrapper.vm.manage()
  expect(push).toHaveBeenLastCalledWith({ path: '/knowledge', query: { agent_id: 'a' } })
  await wrapper.vm.manage('pending')
  expect(push).toHaveBeenLastCalledWith({ path: '/knowledge', query: { agent_id: 'a', status: 'pending' } })
  wrapper.unmount()
})

it('shows catalog overflow notice when eligible and omitted_count > 0, and digest agent notice when ineligible', async () => {
  getPreview.mockResolvedValue({
    items: [],
    indexed_ids: Array.from({ length: 100 }, (_, i) => `e${i}`),
    total_count: 105,
    omitted_count: 5,
    pending_count: 0,
    eligible: true,
    reason: 'eligible',
    injection_enabled: true,
    prompt: '# 知识参考',
    digest_agent_id: 'digest'
  })
  const wrapper = mount(AgentExperienceCards, { props: { agentId: 'a' }, global: { plugins: [ElementPlus] } })
  await flushPromises()
  expect(wrapper.text()).toContain('另有 5 篇可通过搜索读取')
  expect(wrapper.text()).toContain('预览注入内容')
  wrapper.unmount()

  getPreview.mockResolvedValue({
    items: [],
    indexed_ids: [],
    total_count: 0,
    omitted_count: 0,
    pending_count: 0,
    eligible: false,
    reason: 'digest_agent',
    injection_enabled: true,
    prompt: '',
    digest_agent_id: 'digest'
  })
  const digestWrapper = mount(AgentExperienceCards, { props: { agentId: 'digest' }, global: { plugins: [ElementPlus] } })
  await flushPromises()
  expect(digestWrapper.text()).toContain('内置总结智能体不注入知识')
  digestWrapper.unmount()
})
