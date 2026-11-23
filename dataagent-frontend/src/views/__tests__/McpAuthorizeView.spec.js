import { flushPromises, shallowMount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const route = vi.hoisted(() => ({ query: {} }))
const authState = vi.hoisted(() => ({ currentUser: { display_name: '张三' } }))
const decideMcpAuthorization = vi.hoisted(() => vi.fn())

vi.mock('vue-router', () => ({ useRoute: () => route }))
vi.mock('@/stores/auth', () => ({ useAuthStore: () => authState }))
vi.mock('@/api/dataagent', () => ({ dataagentApi: { decideMcpAuthorization } }))

import McpAuthorizeView from '../McpAuthorizeView.vue'

const mount = () => shallowMount(McpAuthorizeView, { global: { stubs: {
  'el-card': { template: '<section><slot /></section>' },
  'el-button': { template: '<button><slot /></button>' },
} } })

describe('MCP consent', () => {
  beforeEach(() => {
    decideMcpAuthorization.mockReset()
    route.query = { client_id: 'dataagent', redirect_uri: 'http://127.0.0.1:7654/cb', state: 'opaque-state' }
  })

  it('shows the registered client, callback host and signed-in user', () => {
    const payload = Buffer.from(JSON.stringify({ client_name: '桌面客户端' })).toString('base64url')
    route.query.client_id = `header.${payload}.signature`
    const wrapper = mount()
    expect(wrapper.findAll('dd').map((element) => element.text())).toEqual(['桌面客户端', '127.0.0.1:7654', '张三'])
    wrapper.unmount()
  })

  it.each([['拒绝', 'deny'], ['允许', 'allow']])('posts the original parameters and the %s decision', async (label, decision) => {
    decideMcpAuthorization.mockResolvedValue({ redirect_url: '#returned-callback' })
    const wrapper = mount()
    try {
      expect(wrapper.text()).toContain('DataAgent')
      await wrapper.findAll('button').find((button) => button.text() === label).trigger('click')
      await flushPromises()
      expect(decideMcpAuthorization).toHaveBeenCalledWith({ ...route.query, decision })
      expect(window.location.hash).toBe('#returned-callback')
    } finally {
      window.history.replaceState(null, '', window.location.pathname)
      wrapper.unmount()
    }
  })
})
