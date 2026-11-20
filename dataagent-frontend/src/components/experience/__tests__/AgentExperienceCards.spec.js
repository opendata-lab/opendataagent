import { flushPromises, mount } from '@vue/test-utils'
import { describe, it, expect, vi } from 'vitest'
import ElementPlus from 'element-plus'
const getPreview = vi.hoisted(() => vi.fn())
const push = vi.hoisted(() => vi.fn())
vi.mock('@/api/dataagent', () => ({ dataagentApi: { getExperienceInjectionPreview: getPreview } }))
vi.mock('vue-router', () => ({ useRouter: () => ({ push }) }))
import AgentExperienceCards from '../AgentExperienceCards.vue'
import ExperienceDrawer from '../ExperienceDrawer.vue'
it('shows both kinds, the server budget, exclusions, read-only detail and management links', async () => {
  const item = { experience_id: 'e1', title: '全局规则', when_text: '查询前', body: '确认结构', kind: 'knowledge', scope: 'global', inject_mode: 'always', status: 'active' }
  getPreview.mockResolvedValue({ items: [item], used: 980, total: 1100, budget: 1000, pending_count: 2,
    skipped_ids: ['e1'], included_ids: [], indexed_ids: [], injection_enabled: false, prompt: '真实注入预览', digest_agent_id: 'digest' })
  const wrapper = mount(AgentExperienceCards, { props: { agentId: 'a' }, global: { plugins: [ElementPlus] } })
  await flushPromises()
  expect(wrapper.text()).toContain('980 / 1000 字')
  expect(wrapper.text()).toContain('2 条待审核')
  expect(wrapper.text()).toContain('部署配置未开启注入')
  expect(wrapper.find('.skipped').text()).toContain('未注入')
  await wrapper.find('li button').trigger('click')
  const drawer = wrapper.findComponent(ExperienceDrawer)
  expect(drawer.props('editing')).toBe(false)
  expect(drawer.props('item')).toEqual(item)
  expect(drawer.text()).not.toContain('保存')
  await wrapper.vm.manage('memory')
  expect(push).toHaveBeenCalledWith({ path: '/memories', query: { agent_id: 'a' } })
  wrapper.unmount()
})
