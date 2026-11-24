import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import ElementPlus from 'element-plus'

const apiMocks = vi.hoisted(() => ({
  listUsers: vi.fn(),
  createUser: vi.fn(),
  getUser: vi.fn(),
  updateUser: vi.fn(),
  resetUserPassword: vi.fn(),
  addUserIdentity: vi.fn(),
  deleteUserIdentity: vi.fn(),
  listRoles: vi.fn(),
  listRolePermissions: vi.fn(),
  createRole: vi.fn(),
  updateRole: vi.fn(),
  deleteRole: vi.fn(),
  listRoleUsers: vi.fn(),
  setRoleUsers: vi.fn(),
  setRoleAgents: vi.fn(),
  listAgentWorkbench: vi.fn(),
  getProfile: vi.fn(),
  updateProfile: vi.fn(),
  changePassword: vi.fn()
}))

const messageMocks = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }))
const confirmMock = vi.hoisted(() => vi.fn())
const pushMock = vi.hoisted(() => vi.fn())
const refreshMeMock = vi.hoisted(() => vi.fn())

vi.mock('@/api/dataagent', () => ({ dataagentApi: apiMocks }))
vi.mock('@/stores/auth', () => ({ useAuthStore: () => ({ refreshMe: refreshMeMock }) }))
vi.mock('vue-router', () => ({ useRouter: () => ({ push: pushMock }) }))
vi.mock('element-plus', async (importOriginal) => ({
  ...(await importOriginal()),
  ElMessage: messageMocks,
  ElMessageBox: { confirm: confirmMock }
}))

import UsersView from '../UsersView.vue'
import RolesView from '../RolesView.vue'
import ProfileView from '../ProfileView.vue'

describe('UsersView, RolesView, ProfileView', () => {
  beforeEach(() => {
    Object.values(apiMocks).forEach((fn) => fn.mockReset())
    messageMocks.success.mockReset()
    messageMocks.error.mockReset()
    confirmMock.mockReset().mockResolvedValue('confirm')
    pushMock.mockReset()
    refreshMeMock.mockReset().mockResolvedValue({})
  })

  it('UsersView lists users, supports creating and disabling (no delete button)', async () => {
    apiMocks.listRoles.mockResolvedValue({
      items: [
        { id: 1, role_key: 'admin', name: '管理员', is_builtin: true },
        { id: 2, role_key: 'user', name: '普通用户', is_builtin: true }
      ]
    })
    apiMocks.listUsers.mockResolvedValue({
      items: [
        {
          id: 2,
          username: 'alice',
          display_name: 'Alice',
          email: 'alice@example.com',
          email_verified: false,
          is_active: true,
          roles: [{ id: 2, role_key: 'user', name: '普通用户' }],
          identities: [{ id: 9, source: 'CorpSSO', subject: 'u_9' }],
          last_login_at: '2026-11-23T10:00:00Z'
        }
      ],
      total: 1
    })
    apiMocks.updateUser.mockResolvedValue({ id: 2, is_active: false })

    const wrapper = mount(UsersView, { global: { plugins: [ElementPlus] }, attachTo: document.body })
    await flushPromises()

    expect(wrapper.text()).toContain('alice')
    expect(wrapper.text()).toContain('Alice')
    expect(wrapper.text()).toContain('未验证')
    expect(wrapper.text()).toContain('停用')
    expect(wrapper.text()).not.toContain('删除用户')

    await wrapper.get('[data-testid="toggle-user-2"]').trigger('click')
    await flushPromises()
    expect(apiMocks.updateUser).toHaveBeenCalledWith(2, { is_active: false })
    wrapper.unmount()
  })

  it('RolesView lists built-in and custom roles and saves role agent grants', async () => {
    apiMocks.listRoles.mockResolvedValue({
      items: [
        { id: 1, role_key: 'admin', name: '管理员', is_builtin: true, member_count: 1, agent_ids: [], permissions: ['users:manage'], menus: ['menu:chat'] },
        { id: 3, role_key: 'analyst', name: '分析师', is_builtin: false, member_count: 2, agent_ids: ['agent_a'], permissions: [], menus: [] }
      ]
    })
    apiMocks.listRolePermissions.mockResolvedValue({ items: [{ key: 'users:manage', label: '用户管理' }] })
    apiMocks.listAgentWorkbench.mockResolvedValue([{ agent_id: 'agent_a', name: '销售助手' }, { agent_id: 'agent_b', name: '财务助手' }])
    apiMocks.setRoleAgents.mockResolvedValue({ id: 3, agent_ids: ['agent_a', 'agent_b'] })

    const wrapper = mount(RolesView, { global: { plugins: [ElementPlus] }, attachTo: document.body })
    await flushPromises()

    expect(wrapper.text()).toContain('管理员')
    expect(wrapper.text()).toContain('分析师')
    expect(wrapper.text()).toContain('全部助手')

    wrapper.vm.$.setupState.openAgentsDialog({ id: 3, role_key: 'analyst', name: '分析师', agent_ids: ['agent_a'] })
    await flushPromises()
    wrapper.vm.$.setupState.selectedAgentIds = ['agent_a', 'agent_b']
    await wrapper.vm.$.setupState.saveAgents()
    expect(apiMocks.setRoleAgents).toHaveBeenCalledWith(3, ['agent_a', 'agent_b'])
    wrapper.unmount()
  })

  it('ProfileView loads profile, saves display_name/email and refreshes auth store', async () => {
    apiMocks.getProfile.mockResolvedValue({
      id: 2,
      username: 'alice',
      display_name: 'Alice',
      email: 'alice@example.com',
      email_verified: false,
      roles: [{ id: 2, role_key: 'user', name: '普通用户' }],
      identities: [{ id: 1, source: 'CorpSSO', subject: 'emp42' }],
      menus: ['menu:chat', 'menu:profile'],
      accessible_agents: [{ agent_id: 'agent_default', name: '默认助手' }]
    })
    apiMocks.updateProfile.mockResolvedValue({
      id: 2,
      username: 'alice',
      display_name: 'Alice Chen',
      email: 'alice.chen@example.com',
      email_verified: false
    })

    const wrapper = mount(ProfileView, { global: { plugins: [ElementPlus] }, attachTo: document.body })
    await flushPromises()

    expect(wrapper.text()).toContain('CorpSSO:emp42')
    expect(wrapper.text()).toContain('默认助手')

    wrapper.vm.$.setupState.profileForm.display_name = 'Alice Chen'
    wrapper.vm.$.setupState.profileForm.email = 'alice.chen@example.com'
    await wrapper.get('[data-testid="save-profile"]').trigger('click')
    await flushPromises()

    expect(apiMocks.updateProfile).toHaveBeenCalledWith({
      display_name: 'Alice Chen',
      email: 'alice.chen@example.com'
    })
    expect(refreshMeMock).toHaveBeenCalled()
    wrapper.unmount()
  })
})
