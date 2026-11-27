const isPlainObject = (value) => value && typeof value === 'object' && !Array.isArray(value)

const parseMaybeJson = (value) => {
  if (typeof value !== 'string') return null
  const raw = value.trim()
  if (!raw) return null
  try {
    return JSON.parse(raw)
  } catch (_error) {
    return null
  }
}

const firstFilled = (...values) => {
  for (const value of values) {
    const text = String(value ?? '').trim()
    if (text) return text
  }
  return ''
}

export const parseToolInput = (value) => {
  if (isPlainObject(value)) return value
  if (typeof value === 'string') {
    const parsed = parseMaybeJson(value)
    if (isPlainObject(parsed)) return parsed
    const text = value.trim()
    return text ? { command: text } : {}
  }
  return {}
}

const buildSearchDetail = ({ pattern, directory, path, description }) => {
  if (pattern && (directory || path)) {
    return `${pattern} · ${directory || path}`
  }
  return firstFilled(pattern, directory, path, description)
}

// The runtime's built-in tools (Pi: bash/read/write/edit/ls/grep/find; older
// SDK records: Glob/Skill), matched by exact name, case-insensitively.
const BUILTIN_KINDS = {
  bash: 'shell',
  read: 'read',
  write: 'edit',
  edit: 'edit',
  ls: 'list',
  grep: 'search',
  find: 'search',
  glob: 'search',
  skill: 'skill'
}

// Runtime tools that are not file traces but still deserve their own label and icon.
const SPECIAL_TOOLS = {
  askuserquestion: { label: '向用户提问', icon: 'question' },
  exitplanmode: { label: '提交计划', icon: 'plan' }
}

// Icons only (never labels) for other tools, from the action words in their name.
const ICON_WORDS = [
  ['sql', ['query', 'sql']],
  ['search', ['search', 'find', 'lookup']],
  ['list', ['list']],
  ['download', ['export', 'download']],
  ['read', ['get', 'describe', 'ddl', 'schema', 'metadata', 'read']],
  ['chart', ['chart', 'plot', 'image']],
  ['edit', ['write', 'update', 'create', 'delete']]
]

const nameWords = (name) => String(name || '')
  .replace(/([a-z0-9])([A-Z])/g, '$1 $2')
  .toLowerCase()
  .split(/[^a-z0-9]+/)
  .filter(Boolean)

export const iconKindFromName = (name) => {
  const words = new Set(nameWords(name))
  for (const [icon, keys] of ICON_WORDS) {
    if (keys.some((key) => words.has(key))) return icon
  }
  return 'tool'
}

const SKILL_LAUNCH_OUTPUT_RE = /^Launching skill(?::\s*(.+))?$/i

export const extractToolSkillName = (tool = {}) => {
  const input = parseToolInput(tool?.input)
  const fromInput = firstFilled(
    input.skill,
    input.skill_name,
    input.skillName,
    input.skill_id,
    input.skillId
  )
  if (fromInput) return fromInput

  const output = String(tool?.output || '').trim()
  const match = output.match(SKILL_LAUNCH_OUTPUT_RE)
  if (match?.[1]) return String(match[1]).trim()

  return ''
}

export const isSkillBootstrapPlaceholder = (tool = {}) => {
  const action = describeToolAction(tool)
  if (action.kind !== 'skill') return false

  const output = String(tool?.output || '').trim()
  if (!output) return true
  return SKILL_LAUNCH_OUTPUT_RE.test(output)
}

export const formatSkillBootstrapLabel = (skillName) => {
  const value = String(skillName || '').trim()
  return value ? `加载技能（${value}）` : '加载技能'
}

export const describeToolAction = (tool = {}) => {
  const name = String(tool?.name || tool?.toolName || '').trim()
  const lowerName = name.toLowerCase()
  const input = parseToolInput(tool?.input)
  const skillName = extractToolSkillName(tool)

  const command = firstFilled(
    input.command,
    input.cmd,
    input.Command,
    input.script,
    input.Script,
    input.argv
  )
  const path = firstFilled(
    input.file_path,
    input.path,
    input.AbsolutePath,
    input.TargetFile,
    input.file,
    input.File
  )
  const directory = firstFilled(
    input.directory,
    input.dir,
    input.SearchDirectory,
    input.Directory,
    input.cwd,
    input.workdir,
    input.root
  )
  const pattern = firstFilled(
    input.pattern,
    input.Pattern,
    input.query,
    input.Query,
    input.keyword,
    input.Keyword,
    input.glob,
    input.Glob,
    input.name,
    input.Name
  )
  const description = firstFilled(
    input.description,
    input.summary,
    input.reason,
    input.purpose,
    input.note
  )

  // Text follows the tool's real identity: only the runtime's own tools get a
  // fixed Chinese label; every other tool (MCP, custom) is shown by its name.
  // MCP tools arrive without an mcp__ prefix, so names are never guessed from
  // substrings like "search" or "read".
  let kind = BUILTIN_KINDS[lowerName] || 'tool'
  if (kind === 'tool' && (lowerName === '' || lowerName === 'tool')) {
    // Older runtimes recorded some calls under a generic "Tool" name; only for
    // that name does the input say what the call was.
    if (skillName) kind = 'skill'
    else if (command) kind = 'shell'
  }
  const special = SPECIAL_TOOLS[lowerName]
  const iconKind = kind !== 'tool' ? kind : (special?.icon || iconKindFromName(name))

  // Render MCP tool names (mcp__<server>__<tool>) as "<server> / <tool>"
  // instead of the raw double-underscore identifier.
  const mcpMatch = name.match(/^mcp__(.+?)__(.+)$/i)
  const displayName = mcpMatch ? `${mcpMatch[1]} / ${mcpMatch[2]}` : name

  const labelMap = {
    shell: '执行命令',
    read: '读取文件',
    list: '查看目录',
    search: '搜索文件',
    edit: '修改文件',
    skill: '执行技能',
    tool: special?.label || (displayName ? `执行工具：${displayName}` : '执行工具')
  }

  let detail = ''
  if (kind === 'shell') {
    detail = firstFilled(command, description, path)
  } else if (kind === 'read') {
    detail = firstFilled(path, description, directory)
  } else if (kind === 'list') {
    detail = firstFilled(directory, path, description)
  } else if (kind === 'search') {
    detail = buildSearchDetail({ pattern, directory, path, description })
  } else if (kind === 'edit') {
    detail = firstFilled(path, directory, description, command)
  } else if (kind === 'skill') {
    detail = firstFilled(skillName, description, command, path, directory, name)
  } else {
    detail = firstFilled(description, command, path, directory, pattern, name)
  }

  return {
    kind,
    iconKind,
    label: labelMap[kind],
    detail,
    preview: detail,
    input,
    name,
    command,
    path,
    directory,
    pattern,
    description,
    isTrace: kind !== 'tool'
  }
}
