import { flushPromises, shallowMount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const routerPush = vi.hoisted(() => vi.fn())
const authState = vi.hoisted(() => ({ isAdmin: true }))
const dataagentApi = vi.hoisted(() =>
  Object.fromEntries(
    [
      'getAgentProfile',
      'getAgentDraft',
      'getAgentBuiltinPrompt',
      'getAgentCapabilities',
      'listAuthUsers',
      'updateAgent',
      'publishAgent',
      'setAgentRemoteMcp',
      'createAgentPreviewTopic',
      'submitAgentPreview',
    ].map((key) => [key, vi.fn()]),
  ),
)
const runtimeApi = vi.hoisted(() => ({ getConfig: vi.fn() }))
const leaveGuard = vi.hoisted(() => vi.fn())
vi.mock('vue-router', () => ({
  useRoute: () => ({ params: { agentId: 'agent_1' }, query: {} }),
  useRouter: () => ({ push: routerPush }),
  onBeforeRouteLeave: leaveGuard,
  onBeforeRouteUpdate: vi.fn(),
}))
vi.mock('@/api/dataagent', () => ({ dataagentApi }))
vi.mock('@/api/nl2sql', () => ({
  DATAAGENT_CLIENT_HEADERS: {},
  createNl2SqlApiClient: () => ({ runtimeApi }),
}))
vi.mock('@/stores/auth', () => ({ useAuthStore: () => authState }))
vi.mock('element-plus', async (original) => ({
  ...(await original()),
  ElMessage: { success: vi.fn(), error: vi.fn() },
  ElMessageBox: { confirm: vi.fn().mockRejectedValue('cancel') },
}))
import AgentDetailView from '../AgentDetailView.vue'
import ChatMcpConfigDialog from '../ChatMcpConfigDialog.vue'

const profile = {
  agent_id: 'agent_1',
  name: '营销分析',
  description: '营销场景',
  system_prompt: '只做营销分析',
  allowed_tools: ['Read'],
  mcp_server_ids: ['web'],
  skill_folders: ['marketing-insights'],
  max_turns: 12,
  env_vars: { SAFE_FLAG: '1' },

  visibility: {
    mode: 'selected',
    allowed_users: ['local:alice'],
    allowed_groups: [],
  },
  is_default: false,
  is_builtin: false,
  revision: 3,
  published_version: 1,
  has_changes: true,
  can_publish: true,
  preview_status: 'finished',
}
const mount = () =>
  shallowMount(AgentDetailView, {
    global: {
      stubs: {
        Icon: true,
        ElSelect: { template: '<div><slot /></div>' },
        ElOption: true,
        ElButton: { props: ['disabled'], template: '<button :disabled="disabled"><slot /></button>' },
        ElPopover: {
          name: 'ElPopover',
          props: ['visible'],
          template: '<div><slot name="reference" /><slot /></div>',
        },
      },
      config: {
        compilerOptions: {
          isCustomElement: (tag) => tag === 'dataagent-conversation',
        },
      },
    },
  })
beforeEach(() => {
  HTMLDialogElement.prototype.close = vi.fn()
  HTMLDialogElement.prototype.showModal = vi.fn()
  authState.isAdmin = true
  Object.values(dataagentApi).forEach((fn) => fn.mockReset())
  dataagentApi.getAgentDraft.mockResolvedValue(structuredClone(profile))
  dataagentApi.getAgentProfile.mockResolvedValue({
    ...profile,
    env_vars: undefined,
    visibility: undefined,
  })
  dataagentApi.getAgentBuiltinPrompt.mockResolvedValue({
    content: '# 真实内置提示词',
  })
  dataagentApi.getAgentCapabilities.mockResolvedValue({
    tools: ['Read'],
    skills: [{ folder: 'marketing-insights', enabled: true }],
    mcp_servers: [{ id: 'web', name: 'Example MCP', tool_names: [] }],
  })
  dataagentApi.listAuthUsers.mockResolvedValue({ items: [] })
  dataagentApi.updateAgent.mockResolvedValue({
    ...profile,
    revision: 4,
    can_publish: true,
    preview_status: null,
  })
  runtimeApi.getConfig.mockResolvedValue({
    providers: [{ provider_id: 'model', models: ['test'], enabled: true }],
    default_provider_id: 'model',
    default_model: 'test',
  })
})

describe('agent draft workbench', () => {
  it('does not label an agent-selected skill as disabled by global fallback settings', async () => {
    dataagentApi.getAgentCapabilities.mockResolvedValue({ tools: ['Read'], skills: [{ folder: profile.skill_folders[0], enabled: false }], mcp_servers: [] })
    const wrapper = mount()
    await flushPromises()
    expect(wrapper.vm.selectedResources('skills')[0].description).toBe('')
    expect(wrapper.text()).not.toContain('未启用')
    wrapper.unmount()
  })
  it('loads the actual builtin prompt and restores saved draft configuration', async () => {
    const wrapper = mount()
    await flushPromises()
    expect(wrapper.find('.builtin-content').text()).toBe('# 真实内置提示词')
    expect(wrapper.find('#custom-prompt').element.value).toBe('只做营销分析')
    expect(wrapper.find('#data-scope').exists()).toBe(false)
    expect(wrapper.vm.canPublish).toBe(true)
    expect(wrapper.findAll('.workbench > .panel')).toHaveLength(3)
    wrapper.unmount()
  })
  it('enables MCP without publishing and opens the shared configuration dialog from the same menu', async () => {
    const path = '/api/v1/remote-agents/agent_1/mcp'
    const live = { ...profile, published_version: 1, remote_mcp_available: true }
    dataagentApi.getAgentDraft.mockResolvedValueOnce({ ...live, remote_mcp_enabled: false, remote_mcp_path: '' })
    let finishToggle
    dataagentApi.setAgentRemoteMcp.mockImplementationOnce(() => new Promise(resolve => { finishToggle = resolve }))
    const wrapper = mount()
    await flushPromises()

    expect(wrapper.get('.mcp-popover-trigger').attributes('aria-label')).toBe('MCP 操作')
    const menu = wrapper.getComponent({ name: 'ElPopover' })
    menu.vm.$emit('update:visible', true)
    await wrapper.vm.$nextTick()
    expect(menu.props('visible')).toBe(true)
    expect(wrapper.find('.mcp-config-action').exists()).toBe(false)
    expect(wrapper.findComponent(ChatMcpConfigDialog).exists()).toBe(false)

    const toggle = wrapper.get('#remote-mcp-switch')
    await toggle.trigger('click')
    expect(toggle.attributes('aria-disabled')).toBe('true')
    expect(wrapper.find('.mcp-config-action').exists()).toBe(false)
    finishToggle({ ...live, remote_mcp_enabled: true, remote_mcp_path: path })
    await flushPromises()

    expect(dataagentApi.setAgentRemoteMcp).toHaveBeenCalledWith('agent_1', true)
    expect(dataagentApi.publishAgent).not.toHaveBeenCalled()
    expect(toggle.attributes('aria-checked')).toBe('true')
    expect(menu.props('visible')).toBe(true)
    await wrapper.get('.mcp-config-action').trigger('click')
    expect(menu.props('visible')).toBe(false)
    expect(wrapper.getComponent(ChatMcpConfigDialog).props()).toMatchObject({
      modelValue: true,
      agentId: 'agent_1',
    })
    wrapper.unmount()
  })

  it('lets a reader open the shared configuration dialog for a published MCP assistant', async () => {
    authState.isAdmin = false
    const path = '/api/v1/remote-agents/agent_1/mcp'
    dataagentApi.getAgentProfile.mockResolvedValueOnce({ ...profile, remote_mcp_path: path })
    const wrapper = mount()
    await flushPromises()

    expect(wrapper.find('#remote-mcp-switch').exists()).toBe(false)
    expect(dataagentApi.getAgentDraft).not.toHaveBeenCalled()
    await wrapper.get('.mcp-config-action').trigger('click')
    expect(wrapper.getComponent(ChatMcpConfigDialog).props('agentId')).toBe('agent_1')
    expect(dataagentApi.setAgentRemoteMcp).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('ignores an older detail response after a newer load has completed', async () => {
    const wrapper = mount()
    await flushPromises()
    let resolveOld
    dataagentApi.getAgentDraft.mockImplementationOnce(() => new Promise(resolve => { resolveOld = resolve }))
    const oldLoad = wrapper.vm.loadDetail()
    dataagentApi.getAgentDraft.mockResolvedValueOnce({ ...profile, name: '最新草稿' })
    await wrapper.vm.loadDetail()
    resolveOld({ ...profile, name: '旧草稿' })
    await oldLoad
    expect(wrapper.vm.form.name).toBe('最新草稿')
    wrapper.unmount()
  })

  it('invalidates publication immediately when editing, and saves with a revision', async () => {
    const wrapper = mount()
    await flushPromises()
    await wrapper.find('#custom-prompt').setValue('更新后的草稿')
    expect(wrapper.vm.canPublish).toBe(false)
    await wrapper.vm.handleSave()
    await flushPromises()
    expect(dataagentApi.updateAgent).toHaveBeenCalledWith(
      'agent_1',
      expect.objectContaining({
        system_prompt: '更新后的草稿',
        expected_revision: 3,
        env_vars: { SAFE_FLAG: '1' },
        visibility: profile.visibility,
      }),
    )
    expect(dataagentApi.publishAgent).not.toHaveBeenCalled()
    expect(wrapper.vm.draft.revision).toBe(4)
    expect(wrapper.vm.canPublish).toBe(true)
    wrapper.unmount()
  })
  it('saves before shared-composer send and rotates an outdated preview without remounting', async () => {
    const wrapper = mount()
    await flushPromises()
    wrapper.vm.previewTopicId = 'preview_old'
    wrapper.vm.previewRevision = 3
    const key = wrapper.vm.conversationKey
    await wrapper.find('#custom-prompt').setValue('新的草稿')
    dataagentApi.updateAgent.mockResolvedValue({ ...profile, revision: 4, system_prompt: '新的草稿', can_publish: false })
    expect(await wrapper.vm.beforePreviewSend()).toBe(true)
    expect(dataagentApi.updateAgent).toHaveBeenCalledWith('agent_1', expect.objectContaining({ expected_revision: 3 }))
    expect(wrapper.vm.previewTopicId).toBe('')
    expect(wrapper.vm.conversationKey).toBe(key)
    expect(wrapper.vm.canPublish).toBe(false)
    wrapper.unmount()
  })
  it('rejects attachments from an earlier draft snapshot and clears the stale endpoint', async () => {
    const wrapper = mount()
    await flushPromises()
    wrapper.vm.previewTopicId = 'preview_old'
    wrapper.vm.previewRevision = 2
    await expect(wrapper.vm.beforePreviewSend({ attachments: [{ relPath: 'uploads/old.csv' }] })).rejects.toThrow('重新添加附件')
    expect(wrapper.vm.previewTopicId).toBe('')
    expect(dataagentApi.submitAgentPreview).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('rejects invalid environment JSON without sending a save request', async () => {
    const wrapper = mount()
    await flushPromises()
    wrapper.vm.envVarsText = '[]'
    expect(await wrapper.vm.handleSave()).toBe(false)
    expect(dataagentApi.updateAgent).not.toHaveBeenCalled()
    wrapper.unmount()
  })
  it('requires explicit acknowledgement before publishing the saved revision without a preview', async () => {
    dataagentApi.getAgentDraft.mockResolvedValue({ ...profile, is_builtin: true, preview_task_id: null, preview_status: null })
    const wrapper = mount()
    await flushPromises()
    await wrapper.vm.handlePublish()
    expect(dataagentApi.publishAgent).not.toHaveBeenCalled()
    wrapper.vm.publishAck = true
    wrapper.vm.publishDialog.close = vi.fn()
    dataagentApi.publishAgent.mockResolvedValue({
      ...profile,
      published_version: 2,
      has_changes: false,
      can_publish: false,
    })
    await wrapper.vm.handlePublish()
    expect(dataagentApi.publishAgent).toHaveBeenCalledWith('agent_1', 3)
    expect(wrapper.vm.draft.published_version).toBe(2)
    wrapper.unmount()
  })
  it('does not overwrite edits made during an in-flight save', async () => {
    const wrapper = mount()
    await flushPromises()
    let resolve
    dataagentApi.updateAgent.mockImplementation(
      () =>
        new Promise((done) => {
          resolve = done
        }),
    )
    await wrapper.find('#agent-name').setValue('第一稿')
    const save = wrapper.vm.handleSave()
    await wrapper.find('#agent-name').setValue('第二稿')
    resolve({ ...profile, name: '第一稿', revision: 4, can_publish: false })
    await save
    expect(wrapper.vm.form.name).toBe('第二稿')
    expect(wrapper.vm.dirty).toBe(true)
    wrapper.unmount()
  })
  it('regular users only load published configuration and cannot save or publish', async () => {
    authState.isAdmin = false
    const wrapper = mount()
    await flushPromises()
    expect(dataagentApi.getAgentDraft).not.toHaveBeenCalled()
    expect(dataagentApi.getAgentCapabilities).not.toHaveBeenCalled()
    expect(wrapper.find('#env-vars').exists()).toBe(false)
    expect(wrapper.find('#visibility').exists()).toBe(false)
    expect(wrapper.find('#custom-prompt').attributes('readonly')).toBeDefined()
    await wrapper.vm.handleSave()
    wrapper.vm.publishAck = true
    await wrapper.vm.handlePublish()
    expect(dataagentApi.updateAgent).not.toHaveBeenCalled()
    expect(dataagentApi.publishAgent).not.toHaveBeenCalled()
    wrapper.unmount()
  })
  it('allows publishing saved changes after a stream error', async () => {
    const wrapper = mount()
    await flushPromises()
    wrapper.vm.onConversationError({ detail: { message: 'SSE 中断' } })
    expect(wrapper.vm.canPublish).toBe(true)
    expect(wrapper.vm.verdict).toBe('SSE 中断')
    wrapper.unmount()
  })
})


it('limits knowledge and memory cards to admin agent editors', async () => {
  const wrapper = mount()
  await flushPromises()
  expect(wrapper.findComponent({ name: 'AgentExperienceCards' }).exists()).toBe(true)
  wrapper.unmount()
  authState.isAdmin = false
  const readonly = mount()
  await flushPromises()
  expect(readonly.findComponent({ name: 'AgentExperienceCards' }).exists()).toBe(false)
  readonly.unmount()
})
