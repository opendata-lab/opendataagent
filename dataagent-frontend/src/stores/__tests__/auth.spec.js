import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

const authApiMock = vi.hoisted(() => ({
  getAuthConfig: vi.fn(),
  login: vi.fn(),
  me: vi.fn(),
  logout: vi.fn(),
  oauthAuthorizeUrl: vi.fn((redirect) => `/api/v1/nl2sql/auth/oauth/authorize?redirect=${encodeURIComponent(redirect || '')}`)
}))

vi.mock('@/api/nl2sql', () => ({
  createNl2SqlApiClient: () => ({ authApi: authApiMock }),
  DATAAGENT_CLIENT_HEADERS: Object.freeze({ 'X-DataAgent-Client': 'dataagent' })
}))

import { sanitizeRedirectPath, useAuthStore } from '../auth'

describe('sanitizeRedirectPath', () => {
  it.each([
    ['http://evil.com/x'],
    ['https://evil.com'],
    ['//evil.com'],
    ['/\\evil'],
    ['javascript:alert(1)'],
    [''],
    [null]
  ])('rejects unsafe value %s', (raw) => {
    expect(sanitizeRedirectPath(raw)).toBe('/chat')
  })

  it('accepts same-origin app paths', () => {
    expect(sanitizeRedirectPath('/chat?x=1')).toBe('/chat?x=1')
  })
})

describe('useAuthStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('loads current user, roles, permissions and menus on bootstrap', async () => {
    authApiMock.getAuthConfig.mockResolvedValue({
      enabled: true,
      provider_name: 'SSO',
      provider_icon: 'fa-github',
      local_login_enabled: true,
      oauth_login_enabled: true
    })
    authApiMock.me.mockResolvedValue({
      id: 2,
      username: 'alice',
      display_name: 'alice',
      roles: [{ id: 2, role_key: 'user', name: '普通用户', is_builtin: true }],
      permissions: ['menu:chat', 'menu:agents', 'menu:profile'],
      menus: ['menu:chat', 'menu:agents', 'menu:profile']
    })
    const store = useAuthStore()
    await store.bootstrap()

    expect(store.enabled).toBe(true)
    expect(store.isAuthenticated).toBe(true)
    expect(store.isAdmin).toBe(false)
    expect(store.hasPermission('menu:chat')).toBe(true)
    expect(store.hasPermission('users:manage')).toBe(false)
    expect(store.currentUser.display_name).toBe('alice')
    expect(store.providerName).toBe('SSO')
    expect(store.providerIcon).toBe('fa-github')
  })

  it('identifies admin role and admin permissions', async () => {
    authApiMock.getAuthConfig.mockResolvedValue({ enabled: true, local_login_enabled: true })
    authApiMock.me.mockResolvedValue({
      id: 1,
      username: 'admin',
      display_name: '管理员',
      roles: [{ id: 1, role_key: 'admin', name: '管理员', is_builtin: true }],
      permissions: ['menu:chat', 'users:manage', 'roles:manage'],
      menus: ['menu:chat']
    })
    const store = useAuthStore()
    await store.bootstrap()

    expect(store.isAdmin).toBe(true)
    expect(store.hasPermission('users:manage')).toBe(true)
  })

  it('swallows the me() 401 and reports unauthenticated', async () => {
    authApiMock.getAuthConfig.mockResolvedValue({ enabled: true, local_login_enabled: true })
    authApiMock.me.mockRejectedValue(new Error('401'))
    const store = useAuthStore()
    await store.bootstrap()

    expect(store.isAuthenticated).toBe(false)
    expect(store.isAdmin).toBe(false)
    expect(store.hasPermission('menu:chat')).toBe(false)
  })

  it('fails closed when config fetch fails', async () => {
    authApiMock.getAuthConfig.mockRejectedValue(new Error('network'))
    const store = useAuthStore()
    await store.bootstrap()

    expect(store.enabled).toBe(true)
    expect(store.isAuthenticated).toBe(false)
    expect(store.isAdmin).toBe(false)
  })

  it('sanitizes the oauth redirect before building the authorize url', () => {
    const store = useAuthStore()
    store.oauthAuthorizeUrl('//evil.com')
    expect(authApiMock.oauthAuthorizeUrl).toHaveBeenCalledWith('')
  })
})
