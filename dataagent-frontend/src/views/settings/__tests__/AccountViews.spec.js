import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import ElementPlus from 'element-plus'

const apiMocks = vi.hoisted(() => ({
  listUsers: vi.fn(),
  createUser: vi.fn(),
  getUser: vi.fn(),
  updateUser: vi.fn(),
  resetUserPassword: vi.fn(),
  listRoles: vi.fn(),
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
          is_active: true,
          roles: [{ id: 2, role_key: 'user', name: '普通用户' }],
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
        { id: 1, role_key: 'admin', name: '管理员', is_builtin: true, user_count: 1, agent_ids: [] },
        { id: 3, role_key: 'analyst', name: '分析师', is_builtin: false, user_count: 2, agent_ids: ['agent_a'] }
      ]
    })
    apiMocks.listAgentWorkbench.mockResolvedValue([{ agent_id: 'agent_a', name: '销售助手' }, { agent_id: 'agent_b', name: '财务助手' }])
    apiMocks.setRoleAgents.mockResolvedValue({ id: 3, agent_ids: ['agent_a', 'agent_b'] })

    const wrapper = mount(RolesView, { global: { plugins: [ElementPlus] }, attachTo: document.body })
    await flushPromises()

    expect(wrapper.text()).toContain('管理员')
    expect(wrapper.text()).toContain('分析师')
    expect(wrapper.text()).toContain('1 人')
    expect(wrapper.text()).toContain('2 人')
    expect(wrapper.text()).toContain('全部助手')
    expect(wrapper.text()).not.toContain('权限说明')

    wrapper.vm.$.setupState.openAgentsDialog({ id: 3, role_key: 'analyst', name: '分析师', agent_ids: ['agent_a'] })
    await flushPromises()
    wrapper.vm.$.setupState.selectedAgentIds = ['agent_a', 'agent_b']
    await wrapper.vm.$.setupState.saveAgents()
    expect(apiMocks.setRoleAgents).toHaveBeenCalledWith(3, ['agent_a', 'agent_b'])
    wrapper.unmount()
  })

  it('ProfileView loads profile, saves display_name and refreshes auth store', async () => {
    apiMocks.getProfile.mockResolvedValue({
      id: 2,
      username: 'alice',
      display_name: 'Alice',
      roles: [{ id: 2, role_key: 'user', name: '普通用户' }],
      login_method: 'oauth'
    })
    apiMocks.updateProfile.mockResolvedValue({
      id: 2,
      username: 'alice',
      display_name: 'Alice Chen',
    })

    const wrapper = mount(ProfileView, { global: { plugins: [ElementPlus] }, attachTo: document.body })
    await flushPromises()

    expect(wrapper.text()).toContain('OAuth')
    expect(wrapper.text()).toContain('普通用户')
    for (const section of ['已绑定外部身份', '可见菜单', '可用智能体']) {
      expect(wrapper.text()).not.toContain(section)
    }

    wrapper.vm.$.setupState.profileForm.display_name = 'Alice Chen'
    await wrapper.get('[data-testid="save-profile"]').trigger('click')
    await flushPromises()

    expect(apiMocks.updateProfile).toHaveBeenCalledWith({
      display_name: 'Alice Chen'
    })
    expect(refreshMeMock).toHaveBeenCalled()
    wrapper.unmount()
  })
})
