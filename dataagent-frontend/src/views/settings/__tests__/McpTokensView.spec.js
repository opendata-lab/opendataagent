import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import ElementPlus from 'element-plus'

const authState = vi.hoisted(() => ({
  enabled: true,
  isAdmin: false,
  currentUser: { user_id: 'local:alice', display_name: 'Alice', role: 'user' }
}))

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

vi.mock('@/stores/auth', () => ({ useAuthStore: () => authState }))
vi.mock('@/api/dataagent', () => ({ dataagentApi: apiMocks }))
vi.mock('@/utils/clipboard', () => ({ copyText: copyTextMock }))
vi.mock('element-plus', async (importOriginal) => ({
  ...(await importOriginal()),
  ElMessage: messageMocks,
  ElMessageBox: { confirm: confirmMock }
}))

import McpTokensView from '../McpTokensView.vue'

const activeToken = {
  id: 1,
  name: 'ZCode 开发机',
  token_prefix: 'dak_abc123',
  user_id: 'local:alice',
  display_name: 'Alice',
  created_at: '2026-11-20T08:00:00Z',
  last_used_at: null,
  expires_at: '2027-02-18T08:00:00Z',
  revoked_at: null,
  status: 'active'
}

const revokedToken = {
  id: 2,
  name: '旧电脑',
  token_prefix: 'dak_old999',
  user_id: 'local:bob',
  display_name: 'Bob',
  created_at: '2026-11-01T08:00:00Z',
  last_used_at: '2026-11-10T10:30:00Z',
  expires_at: null,
  revoked_at: '2026-11-12T09:00:00Z',
  status: 'revoked'
}

const mountView = async (props = {}) => {
  const wrapper = mount(McpTokensView, {
    props,
    global: { plugins: [ElementPlus] },
    attachTo: document.body
  })
  await flushPromises()
  return wrapper
}

describe('McpTokensView', () => {
  beforeEach(() => {
    authState.enabled = true
    authState.isAdmin = false
    authState.currentUser = { user_id: 'local:alice', display_name: 'Alice', role: 'user' }
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
    expect(apiMocks.listMcpTokens).toHaveBeenCalledWith('mine')

    const text = wrapper.text()
    expect(text).toContain('ZCode 开发机')
    expect(text).toContain('dak_abc123…')
    expect(text).toContain('尚未使用')
    expect(text).toContain('有效')

    expect(text).toContain('旧电脑')
    expect(text).toContain('dak_old999…')
    expect(text).toContain('永不过期')
    expect(text).toContain('已撤销')

    expect(wrapper.find('[data-testid="revoke-token-1"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="revoke-token-2"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('hides the admin scope switch and user column for non-admin users', async () => {
    authState.isAdmin = false
    const wrapper = await mountView()

    expect(wrapper.find('[data-testid="scope-switch"]').exists()).toBe(false)
    const headers = wrapper.findAll('thead th').map((th) => th.text())
    expect(headers).not.toContain('用户')
    wrapper.unmount()
  })

  it('lets admins switch between mine and all users and displays the user column', async () => {
    authState.isAdmin = true
    authState.currentUser = { user_id: 'local:admin', display_name: 'Admin', role: 'admin' }
    const wrapper = await mountView()

    expect(wrapper.find('[data-testid="scope-switch"]').exists()).toBe(true)
    expect(apiMocks.listMcpTokens).toHaveBeenCalledWith('mine')
    expect(wrapper.findAll('thead th').map((th) => th.text())).not.toContain('用户')

    apiMocks.listMcpTokens.mockResolvedValueOnce({ items: [activeToken, revokedToken] })
    wrapper.vm.$.setupState.scope = 'all'
    await flushPromises()

    expect(apiMocks.listMcpTokens).toHaveBeenLastCalledWith('all')
    expect(wrapper.findAll('thead th').map((th) => th.text())).toContain('用户')
    expect(wrapper.text()).toContain('Bob')
    wrapper.unmount()
  })

  it('creates a token, displays the plaintext once with copy, and clears it on close', async () => {
    const createdItem = {
      id: 3,
      name: '新客户端',
      token_prefix: 'dak_new123',
      user_id: 'local:alice',
      display_name: 'Alice',
      created_at: '2026-11-22T08:00:00Z',
      last_used_at: null,
      expires_at: null,
      revoked_at: null,
      status: 'active'
    }
    apiMocks.createMcpToken.mockResolvedValueOnce({
      item: createdItem,
      token: 'dak_new123_full_secret_token'
    })

    const wrapper = await mountView()
    await wrapper.get('[data-testid="open-create-token"]').trigger('click')
    await flushPromises()

    // Empty name is rejected client-side.
    await wrapper.get('[data-testid="submit-create-token"]').trigger('click')
    expect(apiMocks.createMcpToken).not.toHaveBeenCalled()

    await wrapper.get('[data-testid="token-name-input"]').setValue('  新客户端  ')
    expect(wrapper.vm.$.setupState.createForm.expiry).toBe('never')
    await wrapper.get('[data-testid="submit-create-token"]').trigger('click')
    await flushPromises()

    expect(apiMocks.createMcpToken).toHaveBeenCalledWith({
      name: '新客户端',
      expires_in_days: null
    })

    const tokenInput = document.body.querySelector('[data-testid="created-token-value"]')
    expect(tokenInput?.value).toBe('dak_new123_full_secret_token')
    expect(document.body.textContent).toContain('无法再次查看')

    document.body.querySelector('[data-testid="copy-created-token"]').click()
    await flushPromises()
    expect(copyTextMock).toHaveBeenCalledWith('dak_new123_full_secret_token')
    expect(messageMocks.success).toHaveBeenCalledWith('已复制令牌')

    document.body.querySelector('[data-testid="close-created-token"]').click()
    await flushPromises()
    expect(wrapper.vm.$.setupState.createdToken).toBe('')
    expect(wrapper.text()).toContain('新客户端')
    wrapper.unmount()
  })

  it('opens creation from the chat entry and exports the newly issued token for its assistant', async () => {
    const path = '/api/v1/remote-agents/agent_sales/mcp'
    apiMocks.getAgentProfile.mockResolvedValue({ name: 'Sales', remote_mcp_path: path })
    apiMocks.createMcpToken.mockResolvedValueOnce({ item: activeToken, token: 'dak_new_test_token' })

    const wrapper = await mountView({ openCreate: true, configAgentId: 'agent_sales' })
    await wrapper.get('[data-testid="token-name-input"]').setValue('Client')
    await wrapper.get('[data-testid="submit-create-token"]').trigger('click')
    await flushPromises()

    const copyButton = wrapper.findAll('button').find((button) => button.text() === '复制 MCP 配置')
    await copyButton.trigger('click')
    expect(apiMocks.createMcpToken).toHaveBeenCalledWith({ name: 'Client', expires_in_days: null })
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

  it('sends the chosen expiry in days when a fixed term is selected', async () => {
    apiMocks.createMcpToken.mockResolvedValueOnce({
      item: { ...activeToken, id: 4, name: '限期令牌', expires_at: '2027-02-20T08:00:00Z' },
      token: 'dak_fixed_secret'
    })
    const wrapper = await mountView()
    await wrapper.get('[data-testid="open-create-token"]').trigger('click')
    await flushPromises()

    await wrapper.get('[data-testid="token-name-input"]').setValue('限期令牌')
    wrapper.vm.$.setupState.createForm.expiry = 30
    await wrapper.get('[data-testid="submit-create-token"]').trigger('click')
    await flushPromises()

    expect(apiMocks.createMcpToken).toHaveBeenCalledWith({
      name: '限期令牌',
      expires_in_days: 30
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
