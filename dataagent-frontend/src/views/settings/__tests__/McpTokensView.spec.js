import { flushPromises, mount, RouterLinkStub } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import ElementPlus from 'element-plus'

const apiMocks = vi.hoisted(() => ({
  getAgentProfile: vi.fn(),
  listMcpTokens: vi.fn(),
  createMcpToken: vi.fn(),
  revokeMcpToken: vi.fn()
}))

const messageMocks = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn()
}))

const confirmMock = vi.hoisted(() => vi.fn())
const copyTextMock = vi.hoisted(() => vi.fn())

vi.mock('@/api/dataagent', () => ({ dataagentApi: apiMocks }))
vi.mock('@/utils/clipboard', () => ({ copyText: copyTextMock }))
vi.mock('element-plus', async (importOriginal) => ({
  ...(await importOriginal()),
  ElMessage: messageMocks,
  ElMessageBox: { confirm: confirmMock }
}))

import McpTokensView from '../McpTokensView.vue'
import McpTokenCreateView from '../McpTokenCreateView.vue'

const activeToken = {
  id: 1,
  name: 'ZCode 开发机',
  token_prefix: 'dak_abc123',
  created_at: '2026-11-20T08:00:00Z',
  last_used_at: null,
  revoked_at: null,
  status: 'active'
}

const revokedToken = {
  id: 2,
  name: '旧电脑',
  token_prefix: 'dak_old999',
  created_at: '2026-11-01T08:00:00Z',
  last_used_at: '2026-11-10T10:30:00Z',
  revoked_at: '2026-11-12T09:00:00Z',
  status: 'revoked'
}

const mountView = async (View = McpTokensView, props = {}) => {
  const wrapper = mount(View, {
    props,
    global: { plugins: [ElementPlus], stubs: { RouterLink: RouterLinkStub } },
    attachTo: document.body
  })
  await flushPromises()
  return wrapper
}

describe('MCP token pages', () => {
  beforeEach(() => {
    apiMocks.getAgentProfile.mockReset()
    apiMocks.listMcpTokens.mockReset().mockResolvedValue({ items: [activeToken, revokedToken] })
    apiMocks.createMcpToken.mockReset()
    apiMocks.revokeMcpToken.mockReset()
    messageMocks.success.mockReset()
    messageMocks.error.mockReset()
    confirmMock.mockReset().mockResolvedValue('confirm')
    copyTextMock.mockReset().mockResolvedValue()
  })

  it('renders the token list with prefix, times, status tags, and active-only revoke action', async () => {
    const wrapper = await mountView()
    expect(apiMocks.listMcpTokens).toHaveBeenCalledWith()

    const text = wrapper.text()
    expect(text).toContain('ZCode 开发机')
    expect(text).toContain('dak_abc123…')
    expect(text).toContain('尚未使用')
    expect(text).toContain('有效')

    expect(text).toContain('旧电脑')
    expect(text).toContain('dak_old999…')
    const headers = wrapper.findAll('thead th').map((th) => th.text())
    expect(headers).toEqual(['名称', '令牌前缀', '创建时间', '最后使用', '状态', '操作'])
    expect(wrapper.find('[data-testid="scope-switch"]').exists()).toBe(false)
    expect(text).toContain('已撤销')

    expect(wrapper.find('[data-testid="revoke-token-1"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="revoke-token-2"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('links from the token list to the dedicated creation page', async () => {
    const wrapper = await mountView()
    const link = wrapper.getComponent(RouterLinkStub)
    expect(link.text()).toBe('新建令牌')
    expect(link.props('to')).toEqual({ name: 'McpTokenCreate' })
    wrapper.unmount()
  })

  it('creates a token with no expiry and shows it on the creation page', async () => {
    const createdItem = {
      id: 3,
      name: '新客户端',
      token_prefix: 'dak_new123',
      created_at: '2026-11-22T08:00:00Z',
      last_used_at: null,
      revoked_at: null,
      status: 'active'
    }
    apiMocks.createMcpToken.mockResolvedValueOnce({
      item: createdItem,
      token: 'dak_new123_full_secret_token'
    })

    const wrapper = await mountView(McpTokenCreateView)
    expect(wrapper.get('h1').text()).toBe('新建访问令牌')

    await wrapper.get('form').trigger('submit')
    expect(apiMocks.createMcpToken).not.toHaveBeenCalled()

    await wrapper.get('[data-testid="token-name-input"]').setValue('  新客户端  ')
    expect(wrapper.find('[data-testid="token-expiry-select"]').exists()).toBe(false)
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    expect(apiMocks.createMcpToken).toHaveBeenCalledWith({
      name: '新客户端'
    })

    expect(wrapper.get('[data-testid="created-token-value"]').element.value).toBe('dak_new123_full_secret_token')
    expect(wrapper.get('h1').text()).toBe('令牌已创建')
    expect(wrapper.text()).toContain('无法再次查看')

    await wrapper.get('[data-testid="copy-created-token"]').trigger('click')
    await flushPromises()
    expect(copyTextMock).toHaveBeenCalledWith('dak_new123_full_secret_token')
    expect(messageMocks.success).toHaveBeenCalledWith('已复制令牌')

    wrapper.unmount()
  })

  it('exports the newly issued token in the originating assistant configuration', async () => {
    const path = '/api/v1/remote-agents/agent_sales/mcp'
    apiMocks.getAgentProfile.mockResolvedValue({ name: 'Sales', remote_mcp_path: path })
    apiMocks.createMcpToken.mockResolvedValueOnce({ item: activeToken, token: 'dak_new_test_token' })

    const wrapper = await mountView(McpTokenCreateView, { configAgentId: 'agent_sales' })
    await wrapper.get('[data-testid="token-name-input"]').setValue('Client')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    const copyButton = wrapper.findAll('button').find((button) => button.text() === '复制 MCP 配置')
    await copyButton.trigger('click')
    expect(apiMocks.createMcpToken).toHaveBeenCalledWith({ name: 'Client' })
    expect(JSON.parse(copyTextMock.mock.lastCall[0])).toEqual({
      mcpServers: {
        'dataagent-Sales': {
          type: 'http',
          url: window.location.origin + path,
          headers: { Authorization: 'Bearer dak_new_test_token' }
        }
      }
    })
    wrapper.unmount()
  })

  it('revokes an active token after confirmation and updates its status', async () => {
    apiMocks.revokeMcpToken.mockResolvedValueOnce({
      item: { ...activeToken, status: 'revoked', revoked_at: '2026-11-22T09:00:00Z' }
    })
    const wrapper = await mountView()

    await wrapper.get('[data-testid="revoke-token-1"]').trigger('click')
    await flushPromises()

    expect(confirmMock).toHaveBeenCalled()
    expect(apiMocks.revokeMcpToken).toHaveBeenCalledWith(1)
    expect(wrapper.find('[data-testid="revoke-token-1"]').exists()).toBe(false)
    expect(messageMocks.success).toHaveBeenCalledWith('令牌已撤销')
    wrapper.unmount()
  })

  it('does not revoke when confirmation is cancelled', async () => {
    confirmMock.mockRejectedValueOnce('cancel')
    const wrapper = await mountView()

    await wrapper.get('[data-testid="revoke-token-1"]').trigger('click')
    await flushPromises()

    expect(apiMocks.revokeMcpToken).not.toHaveBeenCalled()
    expect(wrapper.find('[data-testid="revoke-token-1"]').exists()).toBe(true)
    wrapper.unmount()
  })
})
