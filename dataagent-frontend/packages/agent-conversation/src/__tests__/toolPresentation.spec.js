import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import { KNOWLEDGE_ROOT, describeToolAction, iconKindFromName, redactKnowledgePaths } from '../core/toolPresentation.js'
import ToolOutput from '../ui/ToolOutput.vue'

const act = (name, input = {}) => describeToolAction({ name, input })

describe('describeToolAction', () => {
  it('gives the runtime built-in tools fixed labels by exact name', () => {
    expect(act('bash', { command: 'ls' })).toMatchObject({ kind: 'shell', iconKind: 'shell', label: '执行命令', detail: 'ls' })
    expect(act('read', { path: 'a.md' })).toMatchObject({ kind: 'read', iconKind: 'read', label: '读取文件' })
    expect(act('write', { path: 'a.md' }).kind).toBe('edit')
    expect(act('edit', { path: 'a.md' }).kind).toBe('edit')
    expect(act('ls', { path: '.' }).kind).toBe('list')
    expect(act('grep', { pattern: 'x' }).kind).toBe('search')
    expect(act('find', { pattern: '*.md' })).toMatchObject({ kind: 'search', label: '搜索文件' })
    expect(act('Glob', { pattern: '*.md' }).kind).toBe('search')
    expect(act('Bash', { command: 'pwd' }).kind).toBe('shell')
  })

  it('classifies knowledge read/search by strict KNOWLEDGE_ROOT path boundary without leaking hash paths', () => {
    const hashPath = `${KNOWLEDGE_ROOT}/8f4e2a1b9c0d.md`
    expect(act('read', { path: hashPath })).toMatchObject({
      kind: 'knowledge_read',
      iconKind: 'knowledge',
      label: '查阅知识',
      detail: '',
      isTrace: true
    })

    // With and without trailing slash
    expect(act('grep', { path: KNOWLEDGE_ROOT, pattern: '退款 口径' })).toMatchObject({
      kind: 'knowledge_search',
      iconKind: 'knowledge',
      label: '搜索知识',
      detail: '退款 口径',
      isTrace: true
    })
    expect(act('grep', { path: `${KNOWLEDGE_ROOT}/`, pattern: 'GMV' })).toMatchObject({
      kind: 'knowledge_search',
      iconKind: 'knowledge',
      label: '搜索知识',
      detail: 'GMV'
    })
    expect(act('find', { directory: `${KNOWLEDGE_ROOT}/`, pattern: '*.md' })).toMatchObject({
      kind: 'knowledge_search',
      iconKind: 'knowledge',
      label: '搜索知识',
      detail: '*.md'
    })
    // Never leaks hash path into detail
    expect(act('grep', { path: hashPath }).detail).toBe('')

    // Similar prefix directories and path traversal must NOT match
    expect(act('read', { path: `${KNOWLEDGE_ROOT}_other/a.md` })).toMatchObject({ kind: 'read', iconKind: 'read', label: '读取文件' })
    expect(act('grep', { path: `${KNOWLEDGE_ROOT}2`, pattern: 'x' })).toMatchObject({ kind: 'search', iconKind: 'search', label: '搜索文件' })
    expect(act('read', { path: `${KNOWLEDGE_ROOT}/../other/a.md` })).toMatchObject({ kind: 'read', iconKind: 'read', label: '读取文件' })

    // Normal file reads and bash commands are unaffected
    expect(act('read', { path: '/workspace/report.md' })).toMatchObject({ kind: 'read', iconKind: 'read', detail: '/workspace/report.md' })
    expect(act('bash', { command: `rg 退款 ${KNOWLEDGE_ROOT}` })).toMatchObject({ kind: 'shell', iconKind: 'shell', label: '执行命令' })
  })

  // Real MCP tool names from local sessions; Pi registers them without an mcp__ prefix.
  it('labels other tools by their own name and picks the icon from action words', () => {
    const cases = [
      ['odw_search_tables', 'search'],
      ['search_ontology', 'search'],
      ['odw_query_readonly', 'sql'],
      ['get_ontology', 'read'],
      ['odw_get_table_ddl', 'read'],
      ['odw_export_metadata', 'download'],
      ['odw_resolve_datasource', 'tool'],
      ['smoke_read_large', 'read'],
      ['smoke_write_record', 'edit'],
      ['generate_image', 'chart']
    ]
    for (const [name, icon] of cases) {
      const action = act(name)
      expect(action.kind, name).toBe('tool')
      expect(action.iconKind, name).toBe(icon)
      expect(action.label, name).toBe(`执行工具：${name}`)
    }
  })

  it('shows mcp__server__tool names as server / tool', () => {
    expect(act('mcp__github__search_code')).toMatchObject({ kind: 'tool', iconKind: 'search', label: '执行工具：github / search_code' })
  })

  it('gives AskUserQuestion and ExitPlanMode their own label and icon', () => {
    expect(act('AskUserQuestion', { questions: [] })).toMatchObject({ kind: 'tool', iconKind: 'question', label: '向用户提问' })
    expect(act('ExitPlanMode', { plan: 'x' })).toMatchObject({ kind: 'tool', iconKind: 'plan', label: '提交计划' })
  })

  it('does not infer a file trace from input shape', () => {
    expect(act('custom_tool', { command: 'rm -rf /' }).kind).toBe('tool')
    expect(act('custom_tool', { path: 'a', pattern: 'b' }).kind).toBe('tool')
  })

  it('reads legacy generic Tool records from their input', () => {
    expect(act('Tool', { skill: 'business-domain-assistant' })).toMatchObject({ kind: 'skill', iconKind: 'skill' })
    expect(act('Tool', { command: 'python run.py' }).kind).toBe('shell')
    expect(act('odw_skill_like', { skill: 'x' }).kind).toBe('tool')
  })
})

describe('ToolOutput knowledge presentation', () => {
  it('renders book icon, Chinese summary/status without hash filename, and expands after output arrives', async () => {
    const hashFile = `${KNOWLEDGE_ROOT}/deadbeef1234.md`

    // Running knowledge_read without output: static (not expandable)
    const pendingRead = mount(ToolOutput, {
      props: {
        tool: {
          id: 't1',
          name: 'read',
          input: { path: hashFile },
          status: 'streaming',
          _callComplete: true,
          _runtimeStarted: true
        }
      }
    })
    expect(pendingRead.find('.shell-trace-summary-text').text()).toBe('查阅知识')
    expect(pendingRead.find('.shell-trace-summary-status').text()).toBe('正在查阅')
    expect(pendingRead.text()).not.toContain('deadbeef1234')
    expect(pendingRead.find('button.shell-trace-summary').exists()).toBe(false)
    // Book icon uses 2 paths starting with M2 3h6... and M22 3h-6...
    const paths = pendingRead.findAll('.shell-trace-icon path').map(p => p.attributes('d'))
    expect(paths).toHaveLength(2)
    expect(paths[0]).toContain('M2 3h6')
    pendingRead.unmount()

    // Completed knowledge_read with output: expandable, shows markdown content inside panel
    const doneRead = mount(ToolOutput, {
      props: {
        tool: {
          id: 't2',
          name: 'read',
          input: { path: hashFile },
          output: '# 销售额口径\n适用场景：统计销售额\n\n不含退款订单。',
          status: 'success',
          _callComplete: true,
          _runtimeStarted: true
        }
      }
    })
    expect(doneRead.find('.shell-trace-summary-text').text()).toBe('查阅知识')
    expect(doneRead.find('.shell-trace-summary-status').text()).toBe('已查阅')
    expect(doneRead.text()).not.toContain('deadbeef1234')
    const toggleBtn = doneRead.find('button.shell-trace-summary')
    expect(toggleBtn.exists()).toBe(true)
    await toggleBtn.trigger('click')
    expect(doneRead.text()).toContain('销售额口径')
    expect(doneRead.text()).toContain('不含退款订单')
    expect(doneRead.text()).not.toContain('deadbeef1234')
    doneRead.unmount()

    // Completed knowledge_search with realistic rg output: redacts hashed paths in expanded trace
    const hashA = 'a1b2c3d4e5f60718293a4b5c6d7e8f90'
    const hashB = 'ffeeddccbbaa99887766554433221100'
    const doneSearch = mount(ToolOutput, {
      props: {
        tool: {
          id: 't3',
          name: 'grep',
          input: { path: `${KNOWLEDGE_ROOT}/`, pattern: '退款' },
          output: [
            `${KNOWLEDGE_ROOT}/${hashA}.md:4:销售额不含已退款订单。`,
            `${KNOWLEDGE_ROOT}/${hashA}.md:8:退款按财务确认日统计。`,
            `${KNOWLEDGE_ROOT}/${hashB}.md:2:适用场景：退货与退款分析`
          ].join('\n'),
          status: 'success',
          _callComplete: true,
          _runtimeStarted: true
        }
      }
    })
    expect(doneSearch.find('.shell-trace-summary-text').text()).toBe('搜索知识：退款')
    expect(doneSearch.find('.shell-trace-summary-status').text()).toBe('已搜索')
    await doneSearch.find('button.shell-trace-summary').trigger('click')
    expect(doneSearch.text()).toContain('知识文档 1:4:销售额不含已退款订单。')
    expect(doneSearch.text()).toContain('知识文档 1:8:退款按财务确认日统计。')
    expect(doneSearch.text()).toContain('知识文档 2:2:适用场景：退货与退款分析')
    expect(doneSearch.text()).not.toContain(hashA)
    expect(doneSearch.text()).not.toContain(hashB)
    expect(doneSearch.text()).not.toContain('_knowledge_memory')
    doneSearch.unmount()

    // Completed find with realistic fd output and truncated read with Pi's Source message
    const doneFind = mount(ToolOutput, {
      props: {
        tool: {
          id: 't4',
          name: 'find',
          input: { path: KNOWLEDGE_ROOT, pattern: '*.md' },
          output: `${KNOWLEDGE_ROOT}/${hashA}.md\n${KNOWLEDGE_ROOT}/${hashB}.md`,
          status: 'success',
          _callComplete: true,
          _runtimeStarted: true
        }
      }
    })
    await doneFind.find('button.shell-trace-summary').trigger('click')
    expect(doneFind.text()).toContain('知识文档 1')
    expect(doneFind.text()).toContain('知识文档 2')
    expect(doneFind.text()).not.toContain(hashA)
    expect(doneFind.text()).not.toContain('_knowledge_memory')
    doneFind.unmount()

    const truncatedRead = mount(ToolOutput, {
      props: {
        tool: {
          id: 't5',
          name: 'read',
          input: { path: `${KNOWLEDGE_ROOT}/${hashA}.md` },
          output: `# 销售额口径\n正文内容\nRead preview truncated. Source: ${KNOWLEDGE_ROOT}/${hashA}.md. Use offset/limit for fewer lines, or bash/Python to process a long single-line file.`,
          status: 'success',
          _callComplete: true,
          _runtimeStarted: true
        }
      }
    })
    await truncatedRead.find('button.shell-trace-summary').trigger('click')
    expect(truncatedRead.text()).toContain('Source: 知识文档 1.')
    expect(truncatedRead.text()).not.toContain(hashA)
    expect(truncatedRead.text()).not.toContain('_knowledge_memory')
    truncatedRead.unmount()

    // Non-knowledge paths are left untouched by redactKnowledgePaths
    expect(redactKnowledgePaths('/workspace/report.md:1:hello')).toBe('/workspace/report.md:1:hello')
    expect(redactKnowledgePaths(`${KNOWLEDGE_ROOT}_other/a.md`)).toBe(`${KNOWLEDGE_ROOT}_other/a.md`)
  })
})

describe('iconKindFromName', () => {
  it('splits snake_case, kebab-case and camelCase into words', () => {
    expect(iconKindFromName('listTables')).toBe('list')
    expect(iconKindFromName('run-sql')).toBe('sql')
    expect(iconKindFromName('readonly')).toBe('tool')
  })
})
