import { flushPromises, mount } from '@vue/test-utils'
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
import McpTokenCreateDialog from '../McpTokenCreateDialog.vue'

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

// Dialogs teleport to <body>; read the newest copy.
const last = (selector) => [...document.body.querySelectorAll(selector)].at(-1)
const typeName = (value) => {
  const input = last('[data-testid="token-name-input"]')
  input.value = value
  input.dispatchEvent(new Event('input'))
}

const mountView = async (View = McpTokensView, props = {}) => {
  const wrapper = mount(View, {
    props,
    global: { plugins: [ElementPlus] },
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

  it('opens the creation dialog from the token list and reloads the list after creating', async () => {
    apiMocks.createMcpToken.mockResolvedValueOnce({ item: activeToken, token: 'dak_new_test_token' })
    const wrapper = await mountView()
    const dialog = wrapper.getComponent(McpTokenCreateDialog)
    expect(dialog.props('modelValue')).toBe(false)

    await wrapper.get('[data-testid="open-create-token"]').trigger('click')
    expect(dialog.props('modelValue')).toBe(true)

    await flushPromises()
    typeName('Client')
    last('[data-testid="submit-create-token"]').click()
    await flushPromises()

    expect(apiMocks.createMcpToken).toHaveBeenCalledWith({ name: 'Client' })
    expect(apiMocks.listMcpTokens).toHaveBeenCalledTimes(2)
    expect(last('[data-testid="created-token-value"]').value).toBe('dak_new_test_token')
    wrapper.unmount()
  })

  it('creates a token with no expiry and shows it once in the dialog', async () => {
    apiMocks.createMcpToken.mockResolvedValueOnce({
      item: { ...activeToken, id: 3, name: '新客户端' },
      token: 'dak_new123_full_secret_token'
    })

    const wrapper = await mountView(McpTokenCreateDialog, { modelValue: true })
    const body = document.body
    await flushPromises()
    expect(body.textContent).toContain('新建访问令牌')

    last('[data-testid="submit-create-token"]').click()
    await flushPromises()
    expect(apiMocks.createMcpToken).not.toHaveBeenCalled()

    typeName('  新客户端  ')
    expect(last('[data-testid="token-expiry-select"]')).toBeUndefined()
    last('[data-testid="submit-create-token"]').click()
    await flushPromises()

    expect(apiMocks.createMcpToken).toHaveBeenCalledWith({ name: '新客户端' })
    expect(wrapper.emitted('created')[0][0]).toEqual({
      item: { ...activeToken, id: 3, name: '新客户端' },
      token: 'dak_new123_full_secret_token'
    })
    expect(last('[data-testid="created-token-value"]').value).toBe('dak_new123_full_secret_token')
    expect(body.textContent).toContain('令牌已创建')
    expect(body.textContent).toContain('无法再次查看')

    last('[data-testid="copy-created-token"]').click()
    await flushPromises()
    expect(copyTextMock).toHaveBeenCalledWith('dak_new123_full_secret_token')
    expect(messageMocks.success).toHaveBeenCalledWith('已复制令牌')

    wrapper.unmount()
  })

  it('exports the newly issued token in the originating assistant configuration', async () => {
    const path = '/api/v1/remote-agents/agent_sales/mcp'
    apiMocks.getAgentProfile.mockResolvedValue({ name: 'Sales', remote_mcp_path: path })
    apiMocks.createMcpToken.mockResolvedValueOnce({ item: activeToken, token: 'dak_new_test_token' })

    const wrapper = await mountView(McpTokenCreateDialog, { modelValue: true, configAgentId: 'agent_sales' })
    typeName('Client')
    last('[data-testid="submit-create-token"]').click()
    await flushPromises()

    const copyButton = [...document.body.querySelectorAll('button')].find((button) => button.textContent.trim() === '复制 MCP 配置')
    copyButton.click()
    await flushPromises()
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
