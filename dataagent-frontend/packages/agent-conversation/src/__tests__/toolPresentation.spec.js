import { describe, expect, it } from 'vitest'
import { describeToolAction, iconKindFromName } from '../core/toolPresentation.js'

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

describe('iconKindFromName', () => {
  it('splits snake_case, kebab-case and camelCase into words', () => {
    expect(iconKindFromName('listTables')).toBe('list')
    expect(iconKindFromName('run-sql')).toBe('sql')
    expect(iconKindFromName('readonly')).toBe('tool')
  })
})
