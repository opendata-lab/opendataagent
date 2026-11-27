import { mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'

import PermissionConfirmationCard from '../../../../packages/agent-conversation/src/ui/PermissionCard.vue'

const planBlock = (overrides = {}) => ({
  type: 'permission_request',
  requestId: 'req-1',
  tool_name: 'ExitPlanMode',
  risk_level: 'plan',
  title: '请确认执行计划',
  summary: [
    '## 目标',
    '新建订单明细表并接入日增量加工。',
    '',
    '### 步骤',
    '1. 建表 `dwd_user_order_di`',
    '2. 建 DML 任务',
    '',
    '- 分桶 10',
    '- 副本 3',
  ].join('\n'),
  payload_preview: { plan: 'x' },
  decision: 'pending',
  ...overrides,
})

const writeBlock = (overrides = {}) => ({
  type: 'permission_request',
  requestId: 'req-2',
  tool_name: 'mcp__example__example_create_table',
  risk_level: 'critical',
  title: '请确认操作',
  summary: '新建表 dwd_user_order_di',
  payload_preview: { dbName: 'dwd' },
  decision: 'pending',
  ...overrides,
})

describe('PermissionConfirmationCard plan rendering', () => {
  it('renders the plan body as markdown instead of raw text', () => {
    const wrapper = mount(PermissionConfirmationCard, { props: { block: planBlock() } })
    const body = wrapper.get('.dac-plan-body')

    // Markdown structure is realized as elements, so the plan reads as a document.
    expect(body.findAll('h2').length).toBeGreaterThan(0)
    expect(body.findAll('li').length).toBeGreaterThan(0)
    expect(body.find('code').exists()).toBe(true)
    // ...and the raw markup is not shown to the user.
    expect(body.text()).not.toContain('## 目标')
    expect(body.text()).not.toContain('- 分桶 10')
    expect(body.text()).toContain('目标')
  })

  it('escapes HTML embedded in a model-authored plan', () => {
    const wrapper = mount(PermissionConfirmationCard, {
      props: { block: planBlock({ summary: 'before <img src=x onerror=alert(1)> after' }) },
    })
    const body = wrapper.get('.dac-plan-body')
    expect(body.find('img').exists()).toBe(false)
    expect(body.text()).toContain('<img src=x onerror=alert(1)>')
  })

  it('asks whether to carry out the plan and keeps the plan after a decision', async () => {
    const wrapper = mount(PermissionConfirmationCard, { props: { block: planBlock() } })
    expect(wrapper.get('.dac-plan-ask-title').text()).toBe('实施此计划？')
    const choices = wrapper.findAll('.dac-plan-choice').map((b) => b.text())
    expect(choices[0]).toContain('是，开始执行')
    expect(choices[1]).toContain('先调整计划')

    await wrapper.setProps({ block: planBlock({ decision: 'allow' }) })
    // The decided plan stays readable in the transcript; only the actions collapse.
    expect(wrapper.find('.dac-plan-body').exists()).toBe(true)
    expect(wrapper.findAll('.dac-plan-choice')).toHaveLength(0)
    expect(wrapper.get('.dac-plan-result').text()).toContain('已批准')
  })

  it('emits approval with the request id', async () => {
    const wrapper = mount(PermissionConfirmationCard, { props: { block: planBlock() } })
    await wrapper.findAll('.dac-plan-choice')[0].trigger('click')
    expect(wrapper.emitted('decide')[0]).toEqual([{ requestId: 'req-1', decision: 'allow', plan: true }])
  })

  it('sends a revision request as deny with the user note', async () => {
    const wrapper = mount(PermissionConfirmationCard, { props: { block: planBlock() } })
    await wrapper.findAll('.dac-plan-choice')[1].trigger('click')
    const submit = wrapper.findAll('.dac-plan-btn').find((b) => b.text() === '提交修改意见')
    expect(submit.attributes('disabled')).toBeDefined()

    await wrapper.get('.dac-plan-revise textarea').setValue('先不建汇总表，只做明细层')
    await submit.trigger('click')

    expect(wrapper.emitted('decide')[0]).toEqual([
      { requestId: 'req-1', decision: 'deny', note: '先不建汇总表，只做明细层', plan: true },
    ])
  })

  it('marks a plan approved by the timeout policy', () => {
    const wrapper = mount(PermissionConfirmationCard, {
      props: { block: planBlock({ decision: 'allow', source: 'auto_timeout' }) },
    })
    expect(wrapper.get('.dac-plan-result').text()).toContain('等待超时')
  })

  it('lets the host claim the expanded plan, otherwise expands in place', async () => {
    const wrapper = mount(PermissionConfirmationCard, {
      props: { block: planBlock({ summary: '# 订单主题建模计划\n\n## 目标\n建明细层。' }) },
      attachTo: document.body,
    })
    const seen = []
    const claim = (event) => {
      seen.push(event.detail)
      event.preventDefault()
    }
    document.addEventListener('dataagent-plan-open', claim)
    await wrapper.get('[aria-label="展开"]').trigger('click')
    // The host shows the title in its own header, so the body leaves it out.
    expect(seen[0]).toMatchObject({ title: '订单主题建模计划', body: '## 目标\n建明细层。', requestId: 'req-1' })
    expect(wrapper.find('.dac-plan-body.is-full').exists()).toBe(false)

    document.removeEventListener('dataagent-plan-open', claim)
    await wrapper.get('[aria-label="展开"]').trigger('click')
    expect(wrapper.find('.dac-plan-body.is-full').exists()).toBe(true)
    await wrapper.get('[aria-label="收起"]').trigger('click')
    expect(wrapper.find('.dac-plan-body.is-full').exists()).toBe(false)
    wrapper.unmount()
  })

  // jsdom has no layout: make every element report a body taller than the 240px cap.
  const stubClippedLayout = () => {
    vi.spyOn(HTMLElement.prototype, 'scrollHeight', 'get').mockReturnValue(600)
    vi.spyOn(HTMLElement.prototype, 'clientHeight', 'get').mockReturnValue(240)
  }

  it('shows 查看完整计划 only when the collapsed plan is clipped', async () => {
    const fits = mount(PermissionConfirmationCard, { props: { block: planBlock() } })
    expect(fits.find('.dac-plan-view-btn').exists()).toBe(false)
    expect(fits.find('[aria-label="展开"]').exists()).toBe(true)
    fits.unmount()

    stubClippedLayout()
    const clipped = mount(PermissionConfirmationCard, { props: { block: planBlock() } })
    await nextTick()
    expect(clipped.find('.dac-plan-view-btn').exists()).toBe(true)
    clipped.unmount()
    vi.restoreAllMocks()
  })

  it('opens the plan only from 查看完整计划 or the header expand button, leaving card body free for selection', async () => {
    stubClippedLayout()
    const wrapper = mount(PermissionConfirmationCard, {
      props: { block: planBlock({ summary: '# 订单主题建模计划\n\n查看 [规范](#spec)' }) },
      attachTo: document.body,
    })
    const seen = []
    const claim = (event) => {
      seen.push(event.detail)
      event.preventDefault()
    }
    document.addEventListener('dataagent-plan-open', claim)

    // Clicking the card body, header, tools, or markdown links does not open the plan.
    await wrapper.get('.dac-plan-body').trigger('click')
    await wrapper.get('.dac-plan-head').trigger('click')
    await wrapper.get('[aria-label="复制"]').trigger('click')
    await wrapper.get('.dac-plan-body a').trigger('click')
    expect(seen).toHaveLength(0)
    expect(wrapper.find('.dac-plan-body.is-full').exists()).toBe(false)

    // Clicking "查看完整计划" dispatches dataagent-plan-open and stays collapsed when claimed.
    await wrapper.get('.dac-plan-view-btn').trigger('click')
    expect(seen).toHaveLength(1)
    expect(seen[0]).toMatchObject({ title: '订单主题建模计划', requestId: 'req-1' })
    expect(wrapper.find('.dac-plan-body.is-full').exists()).toBe(false)

    // Without a host listener claiming it, "查看完整计划" expands in place and hides the hover bar.
    document.removeEventListener('dataagent-plan-open', claim)
    await wrapper.get('.dac-plan-view-btn').trigger('click')
    expect(wrapper.find('.dac-plan-body.is-full').exists()).toBe(true)
    expect(wrapper.find('.dac-plan-expand-bar').exists()).toBe(false)

    await wrapper.get('.dac-plan-body').trigger('click')
    expect(wrapper.find('.dac-plan-body.is-full').exists()).toBe(true)

    await wrapper.get('[aria-label="收起"]').trigger('click')
    expect(wrapper.find('.dac-plan-body.is-full').exists()).toBe(false)
    await nextTick()
    expect(wrapper.find('.dac-plan-view-btn').text()).toBe('查看完整计划')
    wrapper.unmount()
    vi.restoreAllMocks()
  })

  it('treats closing the question as declining without notes', async () => {
    const wrapper = mount(PermissionConfirmationCard, { props: { block: planBlock() } })
    await wrapper.get('[aria-label="不执行"]').trigger('click')
    expect(wrapper.emitted('decide')[0]).toEqual([{ requestId: 'req-1', decision: 'deny', plan: true }])
  })

  it('leaves non-plan confirmation cards as plain-text summaries with tool details', () => {
    const wrapper = mount(PermissionConfirmationCard, { props: { block: writeBlock() } })
    expect(wrapper.find('.dac-plan').exists()).toBe(false)
    expect(wrapper.get('.v2-perm-summary').text()).toBe('新建表 dwd_user_order_di')
    expect(wrapper.get('.v2-perm-tool').text()).toContain('example_create_table')
    const buttons = wrapper.findAll('.v2-perm-btn').map((b) => b.text())
    expect(buttons).toContain('允许')
    expect(buttons).toContain('拒绝')
  })
})

describe('PermissionConfirmationCard tool title', () => {
  it('names the tool by its Chinese title instead of the English name', () => {
    const wrapper = mount(PermissionConfirmationCard, {
      props: { block: writeBlock({ tool_name: 'create_ticket', tool_title: '创建工单', title: '请确认写操作', summary: '创建工单' }) },
    })
    expect(wrapper.find('.v2-perm-tool').text()).toBe('工具：创建工单')
    expect(wrapper.find('.v2-perm-summary').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('create_ticket')
  })

  it('falls back to the tool name when there is no title', () => {
    const wrapper = mount(PermissionConfirmationCard, { props: { block: writeBlock() } })
    expect(wrapper.find('.v2-perm-tool').text()).toBe('工具：example_create_table')
  })
})
