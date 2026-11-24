import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'

const USER_PERMS = ['menu:chat', 'menu:agents', 'menu:skills', 'menu:mcp', 'menu:mcp_tokens', 'menu:profile']
const ADMIN_PERMS = [...USER_PERMS, 'models:manage', 'widget:manage', 'users:manage', 'roles:manage']

const logout = vi.fn()
const authState = {
  enabled: true,
  isAdmin: true,
  currentUser: { display_name: 'tester' },
  permissions: [...ADMIN_PERMS],
  hasPermission(key) {
    return this.permissions.includes(key)
  },
  logout
}
vi.mock('@/stores/auth', () => ({ useAuthStore: () => authState }))

const push = vi.fn()
vi.mock('vue-router', () => ({
  useRoute: () => ({ meta: { tab: 'skills' } }),
  useRouter: () => ({ push })
}))

import SettingsLayout from '../SettingsLayout.vue'

const stubs = {
  'el-icon': { template: '<i><slot /></i>' },
  'el-menu': { template: '<div><slot /></div>' },
  'el-menu-item': { props: ['index'], template: '<button><slot /></button>' },
  'el-dropdown': { template: '<div><slot /><slot name="dropdown" /></div>' },
  'el-dropdown-menu': { template: '<div><slot /></div>' },
  'el-dropdown-item': { template: '<div><slot /></div>' },
  'router-view': { template: '<div />' }
}

const mountLayout = () => mount(SettingsLayout, { global: { stubs } })

describe('SettingsLayout', () => {
  beforeEach(() => {
    push.mockClear()
    authState.isAdmin = true
    authState.enabled = true
    authState.currentUser = { display_name: 'tester' }
    authState.permissions = [...ADMIN_PERMS]
  })

  it('groups an admin settings menu including account management and profile', () => {
    const wrapper = mountLayout()
    const text = wrapper.text()

    expect(text).toContain('Agent 能力')
    expect(text).toContain('接入')
    expect(text).toContain('账号与权限')
    expect(text).toContain('个人')
    for (const label of ['Skills', 'MCP 服务', '模型管理', 'Widget 接入', '用户管理', '角色管理', '个人资料', '访问令牌']) {
      expect(text).toContain(label)
    }
  })

  it('hides admin entries when user only has standard permissions', () => {
    authState.isAdmin = false
    authState.permissions = [...USER_PERMS]
    const wrapper = mountLayout()
    const text = wrapper.text()

    expect(text).toContain('Skills')
    expect(text).toContain('MCP 服务')
    expect(text).toContain('个人资料')
    expect(text).toContain('访问令牌')
    for (const hidden of ['模型管理', 'Widget 接入', '接入', '用户管理', '角色管理', '账号与权限']) {
      expect(text).not.toContain(hidden)
    }
  })

  it('offers a way back to the workspace', async () => {
    const wrapper = mountLayout()
    await wrapper.find('.settings-back').trigger('click')
    expect(push).toHaveBeenCalledWith('/chat')
  })

  it('renders user in the footer and supports profile navigation and logout', async () => {
    const wrapper = mountLayout()
    const userTrigger = wrapper.find('.settings-user__trigger')
    expect(userTrigger.exists()).toBe(true)
    expect(userTrigger.text()).toContain('tester')

    await wrapper.vm.handleUserCommand('profile')
    expect(push).toHaveBeenCalledWith('/settings/profile')

    await wrapper.vm.handleUserCommand('logout')
    expect(logout).toHaveBeenCalled()
    expect(push).toHaveBeenCalledWith({ path: '/login', query: { redirect: '/chat' } })
  })

  it('shows 未登录 and routes to login when there is no session', async () => {
    authState.currentUser = null
    authState.permissions = []
    const wrapper = mountLayout()

    const footer = wrapper.find('.settings-footer')
    expect(footer.text()).toContain('未登录')
    expect(footer.text()).not.toContain('退出登录')

    await footer.find('.settings-user__trigger').trigger('click')
    expect(push).toHaveBeenCalledWith({ path: '/login', query: { redirect: '/settings/skills' } })
  })
})
