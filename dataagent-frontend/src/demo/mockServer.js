const clone = (value) => JSON.parse(JSON.stringify(value))


const toPaged = (records, pageNum = 1, pageSize = 10) => {
  const currentPage = Math.max(1, Number(pageNum) || 1)
  const currentSize = Math.max(1, Number(pageSize) || 10)
  const start = (currentPage - 1) * currentSize
  return {
    total: records.length,
    records: records.slice(start, start + currentSize)
  }
}

const parseData = (value) => {
  if (!value) return {}
  if (typeof value === 'string') {
    try {
      return JSON.parse(value)
    } catch (_) {
      return {}
    }
  }
  return value
}

const createResponse = (config, data, status = 200, statusText = 'OK') => Promise.resolve({
  data: {
    code: 200,
    message: 'success',
    data
  },
  status,
  statusText,
  headers: {},
  config,
  request: {}
})

const createRejectedResponse = (config, message, status = 400) => {
  const error = new Error(message)
  error.config = config
  error.response = {
    status,
    data: {
      code: status,
      message
    },
    config,
    headers: {}
  }
  return Promise.reject(error)
}

const createCanceledResponse = (config) => {
  const error = new Error('canceled')
  error.code = 'ERR_CANCELED'
  error.name = 'CanceledError'
  error.config = config
  return Promise.reject(error)
}

const parseRequestUrl = (config) => {
  const rawUrl = String(config.url || '/')
  const rawBaseUrl = String(config.baseURL || '')
  const urlAlreadyIncludesBase = rawBaseUrl && rawUrl.startsWith(rawBaseUrl)
  const urlAlreadyIncludesApiPrefix = rawBaseUrl !== '/api' && rawUrl.startsWith('/api/')
  const combinedUrl = rawBaseUrl
    && !/^[a-z][a-z\d+.-]*:\/\//i.test(rawUrl)
    && !urlAlreadyIncludesBase
    && !urlAlreadyIncludesApiPrefix
    ? `${rawBaseUrl.replace(/\/+$/, '')}/${rawUrl.replace(/^\/+/, '')}`
    : rawUrl
  const url = new URL(combinedUrl || '/', 'https://demo.local')
  const pathname = url.pathname.startsWith('/api/')
    ? url.pathname.slice(4)
    : url.pathname
  const params = new URLSearchParams(url.search)
  Object.entries(config.params || {}).forEach(([key, value]) => {
    if (value === undefined || value === null || value === '') {
      return
    }
    params.set(key, String(value))
  })
  const paramObject = {}
  params.forEach((value, key) => {
    paramObject[key] = value
  })
  return {
    method: String(config.method || 'get').toLowerCase(),
    pathname,
    params: paramObject,
    body: parseData(config.data)
  }
}

const demoProviderSettings = {
  provider_id: 'demo-provider',
  model: 'demo-nl2sql',
  providers: [
    {
      provider_id: 'demo-provider',
      display_name: 'Demo 模型',
      provider_group: '演示',
      provider_enabled: true,
      enabled: true,
      models: ['demo-nl2sql'],
      supported_models: ['demo-nl2sql'],
      custom_models: [],
      default_model: 'demo-nl2sql',
      base_url: 'https://demo.local',
      auth_token_set: true,
      supports_partial_messages: true,
      validation_status: 'verified',
      validation_message: '演示模式使用前端模拟响应。',
      model_detections: {
        'demo-nl2sql': {
          status: 'verified',
          message: '演示模型可用',
          checked_at: '2026-05-22T00:00:00+08:00'
        }
      }
    }
  ],
  widget_allowed_sites: [
    {
      website_id: 'mall-web',
      allow_anonymous: false,
      project_name: '商城官网',
      project_color: '#4A90A4',
      allowed_origins: ['https://mall.demo.example.com']
    },
    {
      website_id: 'ops-console',
      allow_anonymous: false,
      project_name: '运营后台',
      project_color: '#7C5CFC',
      allowed_origins: ['https://ops.demo.example.com']
    }
  ]
}

const demoWidgetMessage = (seq, sender, content, ts) => ({
  message_id: `demo-widget-msg-${seq}`,
  topic_id: '',
  task_id: sender === 'assistant' ? `demo-widget-task-${seq}` : '',
  sender_type: sender,
  type: sender === 'user' ? 'chat' : 'assistant',
  status: 'success',
  content,
  event: '',
  seq_id: seq,
  feedback: '',
  show_in_ui: 1,
  created_at: ts,
  updated_at: ts
})

const demoWidgetTopics = [
  {
    topic_id: 'demo-widget-topic-1',
    title: '最近 7 天的下单转化率',
    chat_topic_id: 'demo-widget-chat-1',
    chat_conversation_id: 'demo-widget-conv-1',
    agent_id: 'agent_default',
    current_task_id: null,
    current_task_status: null,
    message_count: 4,
    last_message_preview: '最近 7 天整体下单转化率为 18.6%，较上周提升 1.2 个百分点。',
    source: 'widget',
    website_id: 'mall-web',
    external_user_id: 'u_10086',
    visitor_id: '',
    created_at: '2026-06-01T09:12:00+08:00',
    updated_at: '2026-06-01T09:15:33+08:00',
    messages: [
      demoWidgetMessage(1, 'user', '最近 7 天的下单转化率是多少？', '2026-06-01T09:12:00+08:00'),
      demoWidgetMessage(2, 'assistant', '正在查询最近 7 天的访问与下单数据……', '2026-06-01T09:12:08+08:00'),
      demoWidgetMessage(3, 'user', '和上一周对比怎么样？', '2026-06-01T09:14:20+08:00'),
      demoWidgetMessage(4, 'assistant', '最近 7 天整体下单转化率为 18.6%，较上周提升 1.2 个百分点，其中移动端贡献了主要增量。', '2026-06-01T09:15:33+08:00')
    ]
  },
  {
    topic_id: 'demo-widget-topic-2',
    title: '华东区门店销售排行',
    chat_topic_id: 'demo-widget-chat-2',
    chat_conversation_id: 'demo-widget-conv-2',
    agent_id: 'agent_default',
    current_task_id: null,
    current_task_status: null,
    message_count: 2,
    last_message_preview: '华东区销售额 Top3 门店为：上海徐汇店、杭州西湖店、南京新街口店。',
    source: 'widget',
    website_id: 'mall-web',
    external_user_id: '',
    visitor_id: 'visitor-7f3a2b',
    created_at: '2026-05-31T20:01:10+08:00',
    updated_at: '2026-05-31T20:02:45+08:00',
    messages: [
      demoWidgetMessage(1, 'user', '帮我看看华东区门店销售排行', '2026-05-31T20:01:10+08:00'),
      demoWidgetMessage(2, 'assistant', '华东区销售额 Top3 门店为：上海徐汇店（¥182.4 万）、杭州西湖店（¥156.7 万）、南京新街口店（¥141.2 万）。', '2026-05-31T20:02:45+08:00')
    ]
  },
  {
    topic_id: 'demo-widget-topic-3',
    title: '昨日风控拦截订单明细',
    chat_topic_id: 'demo-widget-chat-3',
    chat_conversation_id: 'demo-widget-conv-3',
    agent_id: 'agent_default',
    current_task_id: null,
    current_task_status: null,
    message_count: 3,
    last_message_preview: '昨日共拦截 23 笔高风险订单，主要风险原因为「异常收货地址」。',
    source: 'widget',
    website_id: 'ops-console',
    external_user_id: 'ops_zhang',
    visitor_id: '',
    created_at: '2026-05-31T08:30:00+08:00',
    updated_at: '2026-05-31T08:33:12+08:00',
    messages: [
      demoWidgetMessage(1, 'user', '昨天风控拦截了多少订单？', '2026-05-31T08:30:00+08:00'),
      demoWidgetMessage(2, 'assistant', '昨日共拦截 23 笔高风险订单。', '2026-05-31T08:31:40+08:00'),
      demoWidgetMessage(3, 'assistant', '主要风险原因分布：异常收货地址 11 笔、高频下单 7 笔、设备指纹异常 5 笔。', '2026-05-31T08:33:12+08:00')
    ]
  },
  {
    topic_id: 'demo-widget-topic-4',
    title: '会员等级分布',
    chat_topic_id: 'demo-widget-chat-4',
    chat_conversation_id: 'demo-widget-conv-4',
    agent_id: 'agent_default',
    current_task_id: null,
    current_task_status: null,
    message_count: 2,
    last_message_preview: '当前注册会员共 12.8 万人，其中黄金及以上等级占比 21%。',
    source: 'widget',
    website_id: 'ops-console',
    external_user_id: '',
    visitor_id: 'visitor-aa19c0',
    created_at: '2026-05-30T15:48:00+08:00',
    updated_at: '2026-05-30T15:49:05+08:00',
    messages: [
      demoWidgetMessage(1, 'user', '现在各等级会员分别有多少？', '2026-05-30T15:48:00+08:00'),
      demoWidgetMessage(2, 'assistant', '当前注册会员共 12.8 万人，其中普通 79%、白银 0%、黄金 15%、铂金 6%，黄金及以上等级占比 21%。', '2026-05-30T15:49:05+08:00')
    ]
  }
]

const handleDemoWidgetTopics = (params = {}) => {
  const page = Math.max(1, Number(params.page) || 1)
  const pageSize = Math.max(1, Number(params.page_size) || 20)
  const keyword = String(params.keyword || '').trim().toLowerCase()
  const filtered = demoWidgetTopics.filter((topic) => {
    if (params.website_id && topic.website_id !== params.website_id) return false
    if (params.external_user_id && topic.external_user_id !== params.external_user_id) return false
    if (params.visitor_id && topic.visitor_id !== params.visitor_id) return false
    if (params.agent_id && topic.agent_id !== params.agent_id) return false
    if (keyword && !String(topic.title || '').toLowerCase().includes(keyword)) return false
    return true
  })
  const start = (page - 1) * pageSize
  const items = filtered.slice(start, start + pageSize).map((topic) => {
    const { messages, ...summary } = topic
    return clone(summary)
  })
  return { items, total: filtered.length, page, page_size: pageSize }
}

const handleDemoWidgetUsers = (params = {}) => {
  const keyword = String(params.keyword || '').trim().toLowerCase()
  const limit = Math.max(1, Math.min(500, Number(params.limit) || 100))
  const byUser = new Map()
  for (const topic of demoWidgetTopics) {
    if (params.website_id && topic.website_id !== params.website_id) continue
    const ext = String(topic.external_user_id || '').trim()
    const vis = String(topic.visitor_id || '').trim()
    const kind = ext ? 'ext' : (vis ? 'vis' : '')
    const userId = ext || vis
    if (!kind || !userId) continue
    if (keyword && !userId.toLowerCase().includes(keyword)) continue
    const key = `${kind}:${userId}`
    const entry = byUser.get(key) || { kind, user_id: userId, topic_count: 0, last_active_at: '' }
    entry.topic_count += 1
    if (String(topic.updated_at || '') > entry.last_active_at) entry.last_active_at = String(topic.updated_at || '')
    byUser.set(key, entry)
  }
  const items = Array.from(byUser.values())
    .sort((a, b) => (b.last_active_at > a.last_active_at ? 1 : b.last_active_at < a.last_active_at ? -1 : b.topic_count - a.topic_count))
    .slice(0, limit)
    .map(({ kind, user_id, topic_count }) => ({ kind, user_id, topic_count }))
  return { items }
}

const handleDemoWidgetTopicMessages = (topicId, params = {}) => {
  const page = Math.max(1, Number(params.page) || 1)
  const pageSize = Math.max(1, Number(params.page_size) || 200)
  const order = String(params.order || 'asc').toLowerCase() === 'desc' ? 'desc' : 'asc'
  const topic = demoWidgetTopics.find((item) => item.topic_id === topicId)
  if (!topic) return null
  const all = (topic.messages || []).map((msg) => clone({ ...msg, topic_id: topicId }))
  if (order === 'desc') all.reverse()
  const start = (page - 1) * pageSize
  return {
    topic_id: topicId,
    page,
    page_size: pageSize,
    order,
    total: all.length,
    items: all.slice(start, start + pageSize)
  }
}

const demoAgents = [
  {
    agent_id: 'agent_default',
    name: '通用数据问答',
    description: '面向 DataAgent 演示数据的智能问数助手。',
    system_prompt: '你是 DataAgent 演示数据助手。',
    permission_mode: 'inherit',
    allowed_tools: ['Skill', 'Read', 'Grep'],
    mcp_server_ids: [],
    skill_folders: ['dataagent-nl2sql'],
    max_turns: 0,
    env_vars: {},
    is_default: true,
    is_builtin: true
  }
]

const demoReadableAgent = (agent) => {
  const profile = clone(agent)
  delete profile.env_vars
  return profile
}

// Draft state is distinct from the published demo catalog, just as in production.
const demoAgentDrafts = new Map()
const demoDraftFor = (id) => {
  if (!demoAgentDrafts.has(id)) {
    const published = demoAgents.find(agent => agent.agent_id === id)
    if (!published) return null
    demoAgentDrafts.set(id, { ...clone(published), revision: 1, published_version: 1, has_changes: false, can_publish: false })
  }
  return demoAgentDrafts.get(id)
}

const demoSkillDocuments = [
  {
    id: 'skill-doc-1',
    folder: 'dataagent-nl2sql',
    file_name: 'SKILL.md',
    relative_path: 'SKILL.md',
    category: 'root',
    content_type: 'markdown',
    source: 'bundled',
    enabled: true,
    editable: false,
    content: '# dataagent-nl2sql\n\n演示模式下使用前端内置数据回答样例问题。',
    current_content: '# dataagent-nl2sql\n\n演示模式下使用前端内置数据回答样例问题。\n\n## 可回答范围\n\n- 样例表结构、DDL 与预览数据\n- demo_order_detail 的上下游血缘\n- 最近订单、门店销售等演示问数场景',
    updated_at: '2026-05-22T00:00:00+08:00',
    version_count: 1,
    versions: [
      {
        id: 'skill-doc-1-v1',
        version_no: 1,
        is_current: true,
        change_source: 'demo',
        change_summary: '初始化演示 Skill',
        created_at: '2026-05-22T00:00:00+08:00'
      }
    ]
  },
  {
    id: 'skill-doc-2',
    folder: 'dataagent-nl2sql',
    file_name: 'question-routing.md',
    relative_path: 'reference/question-routing.md',
    category: 'reference',
    content_type: 'markdown',
    source: 'bundled',
    enabled: true,
    editable: false,
    content: '优先识别血缘、趋势、表结构和查询类问题。',
    current_content: '# 问题路由\n\n优先识别血缘、趋势、表结构和查询类问题；演示模式下只返回前端内置样例数据。',
    updated_at: '2026-05-22T00:00:00+08:00',
    version_count: 1,
    versions: [
      {
        id: 'skill-doc-2-v1',
        version_no: 1,
        is_current: true,
        change_source: 'demo',
        change_summary: '初始化演示路由说明',
        created_at: '2026-05-22T00:00:00+08:00'
      }
    ]
  }
]

let demoTopicSeq = 1
let demoTaskSeq = 1
const demoTopics = [
  {
    topic_id: 'demo-topic-chart',
    title: '最近 7 天工作流执行成功率趋势',
    agent_id: 'agent_default',
    message_count: 2,
    current_task_id: '',
    current_task_status: 'finished',
    last_message_preview: '成功率稳定在 96% 以上，6/23 达到峰值 99.1%',
    created_at: '2026-05-24T14:20:00+08:00',
    updated_at: '2026-05-24T14:20:32+08:00',
    messages: [
      {
        message_id: 'demo-chart-user-1',
        sender_type: 'user',
        role: 'user',
        content: '最近 7 天工作流执行成功率趋势',
        created_at: '2026-05-24T14:20:00+08:00'
      },
      {
        message_id: 'demo-chart-assistant-1',
        sender_type: 'assistant',
        role: 'assistant',
        content: '最近 7 天工作流执行成功率稳定在 96% 以上，5 月 23 日达到峰值 99.1%；5 月 20 日略低（95.8%），主要受当天批量重跑影响。整体执行质量健康。',
        status: 'finished',
        feedback: 'like',
        task_id: 'demo-task-chart',
        blocks: [
          {
            block_id: 'chart-think',
            type: 'thinking',
            status: 'success',
            text: '问题类型：趋势分析。指标为工作流执行成功率（成功实例数 / 总实例数），按天聚合，时间范围最近 7 天。命中 task_instance 的只读快路径，直接生成 SQL 并执行。'
          },
          {
            block_id: 'chart-sql',
            type: 'tool_use',
            status: 'success',
            is_error: false,
            tool_id: 'demo-tool-chart-sql',
            tool_name: 'run_sql.py',
            input: {
              sql: 'SELECT stat_day, total_cnt, success_rate FROM wf_exec_daily_stat WHERE stat_day >= DATE_SUB(CURDATE(), INTERVAL 7 DAY) ORDER BY stat_day'
            },
            output: {
              kind: 'sql_execution',
              sql: 'SELECT stat_day, total_cnt, success_rate FROM wf_exec_daily_stat WHERE stat_day >= DATE_SUB(CURDATE(), INTERVAL 7 DAY) ORDER BY stat_day',
              columns: ['stat_day', 'total_cnt', 'success_rate'],
              rows: [
                { stat_day: '2026-05-18', total_cnt: 120, success_rate: 96.2 },
                { stat_day: '2026-05-19', total_cnt: 134, success_rate: 97.1 },
                { stat_day: '2026-05-20', total_cnt: 128, success_rate: 95.8 },
                { stat_day: '2026-05-21', total_cnt: 142, success_rate: 98.0 },
                { stat_day: '2026-05-22', total_cnt: 138, success_rate: 97.6 },
                { stat_day: '2026-05-23', total_cnt: 151, success_rate: 99.1 },
                { stat_day: '2026-05-24', total_cnt: 147, success_rate: 98.4 }
              ],
              row_count: 7,
              duration_ms: 23,
              error: null
            }
          },
          {
            block_id: 'chart-spec',
            type: 'tool_use',
            status: 'success',
            is_error: false,
            tool_id: 'demo-tool-chart-spec',
            tool_name: 'build_chart_spec.py',
            output: {
              kind: 'chart_spec',
              version: 1,
              chart_type: 'line',
              title: '最近 7 天工作流执行成功率趋势',
              description: '单位：%',
              x_field: 'stat_day',
              unit: '%',
              series: [{ name: '成功率', field: 'success_rate', type: 'line' }],
              dataset: [
                { stat_day: '2026-05-18', success_rate: 96.2 },
                { stat_day: '2026-05-19', success_rate: 97.1 },
                { stat_day: '2026-05-20', success_rate: 95.8 },
                { stat_day: '2026-05-21', success_rate: 98.0 },
                { stat_day: '2026-05-22', success_rate: 97.6 },
                { stat_day: '2026-05-23', success_rate: 99.1 },
                { stat_day: '2026-05-24', success_rate: 98.4 }
              ],
              error: null
            }
          },
          {
            block_id: 'chart-main',
            type: 'main_text',
            status: 'success',
            text: '最近 7 天工作流执行成功率稳定在 96% 以上，5 月 23 日达到峰值 99.1%；5 月 20 日略低（95.8%），主要受当天批量重跑影响。整体执行质量健康。'
          }
        ],
        created_at: '2026-05-24T14:20:32+08:00'
      }
    ]
  },
  {
    topic_id: 'demo-topic-running',
    title: '最近 30 天工作流发布次数趋势',
    agent_id: 'agent_default',
    message_count: 2,
    current_task_id: 'demo-task-running',
    current_task_status: 'running',
    last_message_preview: '正在生成只读 SQL 并执行……',
    created_at: '2026-05-24T09:30:00+08:00',
    updated_at: '2026-05-24T09:31:12+08:00',
    messages: [
      {
        message_id: 'demo-running-user-1',
        sender_type: 'user',
        role: 'user',
        content: '最近 30 天工作流发布次数趋势',
        created_at: '2026-05-24T09:30:00+08:00'
      },
      {
        message_id: 'demo-running-assistant-1',
        sender_type: 'assistant',
        role: 'assistant',
        content: '',
        status: 'running',
        feedback: '',
        task_id: 'demo-task-running',
        blocks: [
          {
            block_id: 'running-think',
            type: 'thinking',
            status: 'streaming',
            text: '问题类型：趋势分析。指标为 workflow_publish_record 的发布次数，按天聚合，时间范围最近 30 天。正在生成只读 SQL 并执行……'
          },
          {
            block_id: 'running-tool',
            type: 'tool_use',
            status: 'streaming',
            tool_id: 'demo-tool-running',
            tool_name: 'run_sql',
            input: { question: '最近 30 天工作流发布次数趋势' },
            output: ''
          }
        ],
        created_at: '2026-05-24T09:31:12+08:00'
      }
    ]
  },
  {
    topic_id: 'demo-topic-error',
    title: '各数据层表数量对比',
    agent_id: 'agent_default',
    message_count: 2,
    current_task_id: 'demo-task-error',
    current_task_status: 'error',
    last_message_preview: 'SQL 执行失败：字段 layer_name 不存在',
    created_at: '2026-05-23T16:10:00+08:00',
    updated_at: '2026-05-23T16:10:48+08:00',
    messages: [
      {
        message_id: 'demo-error-user-1',
        sender_type: 'user',
        role: 'user',
        content: '各数据层表数量对比',
        created_at: '2026-05-23T16:10:00+08:00'
      },
      {
        message_id: 'demo-error-assistant-1',
        sender_type: 'assistant',
        role: 'assistant',
        content: '',
        status: 'error',
        feedback: '',
        task_id: 'demo-task-error',
        error: {
          code: 'sql_execution_error',
          message: 'SQL 执行失败：字段 layer_name 在表 dim_data_layer 中不存在，请确认口径后重试。'
        },
        blocks: [
          {
            block_id: 'error-think',
            type: 'thinking',
            status: 'success',
            text: '问题类型：分布对比。需要按数据层统计表数量，候选维度表为 dim_data_layer，事实表为 meta_table_catalog。'
          },
          {
            block_id: 'error-tool',
            type: 'tool_use',
            status: 'failed',
            is_error: true,
            tool_id: 'demo-tool-error',
            tool_name: 'run_sql',
            input: { sql: 'SELECT layer_name, COUNT(*) FROM dim_data_layer GROUP BY layer_name' },
            output: 'ERROR 1054 (42S22): Unknown column \'layer_name\' in \'field list\''
          }
        ],
        created_at: '2026-05-23T16:10:48+08:00'
      }
    ]
  },
  {
    topic_id: 'demo-topic-suspended',
    title: '有哪些失败的任务实例',
    agent_id: 'agent_default',
    message_count: 2,
    current_task_id: 'demo-task-suspended',
    current_task_status: 'suspended',
    last_message_preview: '任务已被用户取消',
    created_at: '2026-05-23T11:05:00+08:00',
    updated_at: '2026-05-23T11:05:26+08:00',
    messages: [
      {
        message_id: 'demo-suspended-user-1',
        sender_type: 'user',
        role: 'user',
        content: '有哪些失败的任务实例',
        created_at: '2026-05-23T11:05:00+08:00'
      },
      {
        message_id: 'demo-suspended-assistant-1',
        sender_type: 'assistant',
        role: 'assistant',
        content: '正在统计最近失败的任务实例……（已被用户取消）',
        status: 'suspended',
        feedback: '',
        task_id: 'demo-task-suspended',
        error: { code: 'task_cancelled', message: '任务已取消' },
        blocks: [
          {
            block_id: 'suspended-think',
            type: 'thinking',
            status: 'success',
            text: '问题类型：明细筛选。需要从 task_instance 中筛选 status = failed 的记录，按结束时间倒序返回。'
          },
          {
            block_id: 'suspended-text',
            type: 'main_text',
            status: 'streaming',
            text: '正在统计最近失败的任务实例……（已被用户取消）'
          }
        ],
        created_at: '2026-05-23T11:05:26+08:00'
      }
    ]
  },
  {
    topic_id: 'demo-topic-1',
    title: '演示问数会话',
    agent_id: 'agent_default',
    message_count: 2,
    current_task_id: '',
    current_task_status: 'finished',
    last_message_preview: 'demo_order_detail 的上下游血缘',
    created_at: '2026-05-22T10:00:00+08:00',
    updated_at: '2026-05-22T10:02:00+08:00',
    messages: [
      {
        message_id: 'demo-user-1',
        sender_type: 'user',
        role: 'user',
        content: '查看 demo_order_detail 的上下游血缘',
        created_at: '2026-05-22T10:00:00+08:00'
      },
      {
        message_id: 'demo-assistant-1',
        sender_type: 'assistant',
        role: 'assistant',
        content: 'demo_order_detail 上游来自 demo_order_event_raw 与 demo_member_profile，下游产出 demo_store_sales_daily 和 demo_order_risk_alert。',
        status: 'finished',
        feedback: '',
        blocks: [
          {
            block_id: 'main-text',
            type: 'main_text',
            status: 'success',
            text: 'demo_order_detail 上游来自 demo_order_event_raw 与 demo_member_profile，下游产出 demo_store_sales_daily 和 demo_order_risk_alert。'
          }
        ],
        created_at: '2026-05-22T10:02:00+08:00'
      }
    ]
  }
]
const demoTasks = {}

const publicTopic = (topic) => {
  const { messages, ...summary } = topic
  return {
    ...summary,
    message_count: Array.isArray(messages) ? messages.length : Number(summary.message_count || 0)
  }
}

const findDemoTopic = (topicId) => demoTopics.find((topic) => String(topic.topic_id) === String(topicId))

const demoNow = () => new Date().toISOString()

const handleDemoSettingsUpdate = (body = {}) => {
  if (Array.isArray(body.providers)) {
    body.providers.forEach((patch) => {
      const provider = demoProviderSettings.providers.find((item) => item.provider_id === patch.provider_id)
      if (!provider) return
      Object.assign(provider, {
        provider_enabled: patch.provider_enabled ?? provider.provider_enabled,
        enabled: patch.provider_enabled ?? provider.enabled,
        base_url: patch.base_url ?? provider.base_url,
        supports_partial_messages: patch.supports_partial_messages ?? provider.supports_partial_messages,
        custom_models: Array.isArray(patch.custom_models) ? patch.custom_models : provider.custom_models,
        models: Array.isArray(patch.enabled_models) ? patch.enabled_models : provider.models,
        model_detections: patch.model_detections || provider.model_detections,
        auth_token_set: true
      })
    })
  }
  if (body.provider_id) demoProviderSettings.provider_id = body.provider_id
  if (body.model) demoProviderSettings.model = body.model
  return clone(demoProviderSettings)
}

export const demoAdapter = async (config) => {
  if (config.signal?.aborted) {
    return createCanceledResponse(config)
  }

  const { method, pathname, params, body } = parseRequestUrl(config)

  if (method === 'get' && pathname === '/v1/tasks/config/dolphin-webui') {
    return createResponse(config, { webuiUrl: '' })
  }

  if (method === 'get' && pathname === '/v1/nl2sql/runtime-config') {
    return createResponse(config, {
      demo: true,
      supports_async_tasks: true,
      stream_transport: 'mock'
    })
  }

  if (method === 'get' && pathname === '/v1/nl2sql/health') {
    return createResponse(config, { status: 'ok', mode: 'demo' })
  }

  // 演示模式不启用认证：SPA 路由守卫与登录页保持关闭。
  if (method === 'get' && pathname === '/v1/nl2sql/auth/config') {
    return createResponse(config, {
      enabled: false,
      provider_name: '',
      provider_icon: '',
      local_login_enabled: false,
      oauth_login_enabled: false
    })
  }

  if (method === 'get' && pathname === '/v1/nl2sql-admin/topics') {
    return createResponse(config, handleDemoWidgetTopics(params))
  }

  if (method === 'get' && pathname === '/v1/nl2sql-admin/settings') {
    return createResponse(config, clone(demoProviderSettings))
  }

  if (method === 'put' && pathname === '/v1/nl2sql-admin/settings') {
    return createResponse(config, handleDemoSettingsUpdate(body))
  }

  if (method === 'post' && pathname === '/v1/nl2sql-admin/model-detections') {
    return createResponse(config, {
      status: 'verified',
      message: '演示模型检测通过',
      checked_at: demoNow()
    })
  }

  if (method === 'get' && pathname === '/v1/nl2sql-admin/widget-topics') {
    return createResponse(config, handleDemoWidgetTopics(params))
  }

  if (method === 'get' && pathname === '/v1/nl2sql-admin/widget-users') {
    return createResponse(config, handleDemoWidgetUsers(params))
  }

  if (method === 'get' && pathname.match(/^\/v1\/nl2sql-admin\/widget-topics\/[^/]+\/messages$/)) {
    const topicId = decodeURIComponent(pathname.split('/')[4])
    const payload = handleDemoWidgetTopicMessages(topicId, params)
    return payload
      ? createResponse(config, payload)
      : createRejectedResponse(config, '会话不存在', 404)
  }

  if (method === 'get' && pathname === '/v1/dataagent/agents/workbench') {
    demoAgents.forEach(agent => demoDraftFor(agent.agent_id))
    return createResponse(config, clone([...demoAgentDrafts.values()]))
  }
  if (method === 'get' && pathname === '/v1/dataagent/agents/builtin-prompt') {
    return createResponse(config, { content: '# DataAgent\n\n遵守数据授权，依据结构证据生成 SQL，使用简体中文回复。' })
  }
  if (method === 'get' && pathname.match(/^\/v1\/dataagent\/agents\/[^/]+\/draft$/)) {
    const draft = demoDraftFor(decodeURIComponent(pathname.split('/')[4]))
    return draft ? createResponse(config, clone(draft)) : createRejectedResponse(config, '智能体不存在', 404)
  }
  if (method === 'post' && pathname.match(/^\/v1\/dataagent\/agents\/[^/]+\/publish$/)) {
    const draft = demoDraftFor(decodeURIComponent(pathname.split('/')[4]))
    if (!draft) return createRejectedResponse(config, '智能体不存在', 404)
    if (body.expected_revision !== draft.revision) return createRejectedResponse(config, '草稿已更新，请刷新后重试', 409)
    if (!draft.has_changes) return createRejectedResponse(config, '当前草稿已发布，没有新的修改', 409)
    draft.published_version++; draft.has_changes = false; draft.can_publish = false
    const index = demoAgents.findIndex(agent => agent.agent_id === draft.agent_id)
    if (index < 0) demoAgents.push(clone(draft))
    else demoAgents[index] = clone(draft)
    return createResponse(config, clone(draft))
  }
  if (method === 'post' && pathname.match(/^\/v1\/dataagent\/agents\/[^/]+\/preview-topics$/)) {
    const draft = demoDraftFor(decodeURIComponent(pathname.split('/')[4]))
    if (!draft) return createRejectedResponse(config, '智能体不存在', 404)
    if (body.expected_revision !== draft.revision) return createRejectedResponse(config, '草稿已更新', 409)
    const topic = { topic_id: `demo-topic-${++demoTopicSeq}`, title: `调试 · ${draft.name}`, agent_id: draft.agent_id, is_agent_preview: true, current_task_id: '', current_task_status: '', messages: [], created_at: demoNow(), updated_at: demoNow() }
    demoTopics.unshift(topic)
    return createResponse(config, clone(publicTopic(topic)))
  }
  if (method === 'post' && pathname.match(/^\/v1\/dataagent\/agents\/[^/]+\/preview-tasks$/)) {
    const draft = demoDraftFor(decodeURIComponent(pathname.split('/')[4]))
    const topic = findDemoTopic(body.topic_id)
    if (!draft || !topic?.is_agent_preview || topic.agent_id !== draft.agent_id) return createRejectedResponse(config, '预览话题不存在', 404)
    if (body.expected_revision !== draft.revision) return createRejectedResponse(config, '草稿已更新', 409)
    // Preview remains unavailable; publishing saved changes does not require a run.
    return createRejectedResponse(config, '演示环境不执行真实调试，请在本地或部署环境中调试', 400)
  }

  if (method === 'get' && pathname === '/v1/dataagent/agents') {
    return createResponse(config, clone(demoAgents))
  }

  if (method === 'get' && pathname === '/v1/dataagent/agents/profiles') {
    return createResponse(config, demoAgents.map(demoReadableAgent))
  }

  if (method === 'get' && pathname === '/v1/dataagent/agents/capabilities') {
    return createResponse(config, {
      tools: ['Skill', 'Read', 'Grep', 'Bash'],
      mcp_servers: [],
      skills: demoSkillDocuments
        .filter((item) => item.file_name === 'SKILL.md')
        .map((item) => ({ folder: item.folder, name: item.folder, enabled: item.enabled })),
      permission_modes: ['inherit', 'allow_all', 'restricted']
    })
  }

  if (method === 'get' && pathname.match(/^\/v1\/dataagent\/agents\/[^/]+$/)) {
    const agentId = decodeURIComponent(pathname.split('/').pop())
    const agent = demoAgents.find((item) => item.agent_id === agentId)
    return agent
      ? createResponse(config, clone(agent))
      : createRejectedResponse(config, '智能体不存在', 404)
  }

  if (method === 'get' && pathname.match(/^\/v1\/dataagent\/agents\/[^/]+\/profile$/)) {
    const agentId = decodeURIComponent(pathname.split('/')[4])
    const agent = demoAgents.find((item) => item.agent_id === agentId)
    return agent
      ? createResponse(config, demoReadableAgent(agent))
      : createRejectedResponse(config, '智能体不存在', 404)
  }

  if (method === 'get' && pathname.match(/^\/v1\/dataagent\/agents\/[^/]+\/configuration$/)) {
    const agentId = decodeURIComponent(pathname.split('/')[4])
    const agent = demoAgents.find((item) => item.agent_id === agentId)
    return agent
      ? createResponse(config, clone(agent))
      : createRejectedResponse(config, '智能体不存在', 404)
  }

  if (method === 'post' && pathname === '/v1/dataagent/agents') {
    const next = { ...clone(demoAgents[0]), ...body, agent_id: `agent_demo_draft_${demoAgentDrafts.size + 1}`, is_default: false, is_builtin: false, revision: 1, published_version: 0, has_changes: true, can_publish: true }
    demoAgentDrafts.set(next.agent_id, next)
    return createResponse(config, clone(next))
  }
  if (method === 'put' && pathname.match(/^\/v1\/dataagent\/agents\/[^/]+$/)) {
    const agentId = decodeURIComponent(pathname.split('/').pop()), draft = demoDraftFor(agentId)
    if (!draft) return createRejectedResponse(config, '智能体不存在', 404)
    if (body.expected_revision !== draft.revision) return createRejectedResponse(config, '草稿已更新', 409)
    const { expected_revision, ...payload } = body
    Object.assign(draft, payload, { revision: draft.revision + 1, has_changes: true, can_publish: true, preview_status: null })
    return createResponse(config, clone(draft))
  }
  if (method === 'delete' && pathname.match(/^\/v1\/dataagent\/agents\/[^/]+$/)) {
    const agentId = decodeURIComponent(pathname.split('/').pop())
    demoAgentDrafts.delete(agentId)
    const index = demoAgents.findIndex(agent => agent.agent_id === agentId)
    if (index >= 0 && !demoAgents[index].is_builtin) demoAgents.splice(index, 1)
    return createResponse(config, { status: 'ok' })
  }

  if (method === 'get' && pathname === '/v1/dataagent/data-scope/options') {
    return createResponse(config, [
      {
        cluster_id: DEMO_CLUSTER_ID,
        source_type: 'DORIS',
        database: DEMO_DATABASE,
        label: 'README 演示集群 / example'
      }
    ])
  }

  if (method === 'get' && pathname === '/v1/dataagent/skills/documents') {
    return createResponse(config, clone(demoSkillDocuments))
  }

  if (method === 'get' && pathname.match(/^\/v1\/dataagent\/skills\/documents\/[^/]+$/)) {
    const documentId = decodeURIComponent(pathname.split('/').pop())
    const document = demoSkillDocuments.find((item) => String(item.id) === documentId)
    return document
      ? createResponse(config, clone(document))
      : createRejectedResponse(config, 'Skill 文档不存在', 404)
  }

  if (method === 'put' && pathname.match(/^\/v1\/dataagent\/skills\/documents\/[^/]+$/)) {
    const documentId = decodeURIComponent(pathname.split('/').pop())
    const document = demoSkillDocuments.find((item) => String(item.id) === documentId)
    if (!document) return createRejectedResponse(config, 'Skill 文档不存在', 404)
    document.content = String(body.content || document.content || '')
    document.current_content = document.content
    document.updated_at = demoNow()
    return createResponse(config, clone(document))
  }

  if (method === 'put' && pathname.match(/^\/v1\/dataagent\/skills\/runtime\/[^/]+$/)) {
    const folder = decodeURIComponent(pathname.split('/').pop())
    demoSkillDocuments.forEach((document) => {
      if (document.folder === folder) document.enabled = Boolean(body.enabled)
    })
    return createResponse(config, { folder, enabled: Boolean(body.enabled) })
  }

  if (method === 'post' && pathname.match(/^\/v1\/dataagent\/skills\/documents\/[^/]+\/compare$/)) {
    return createResponse(config, { changed: false, diff: '' })
  }

  if (method === 'post' && pathname.match(/^\/v1\/dataagent\/skills\/documents\/[^/]+\/versions\/[^/]+\/rollback$/)) {
    const documentId = decodeURIComponent(pathname.split('/')[5])
    const document = demoSkillDocuments.find((item) => String(item.id) === documentId)
    return document
      ? createResponse(config, clone(document))
      : createRejectedResponse(config, 'Skill 文档不存在', 404)
  }

  if (method === 'post' && pathname === '/v1/dataagent/skills/imports') {
    return createResponse(config, { skill_id: 'demo-imported-skill' })
  }

  if (method === 'get' && pathname.match(/^\/v1\/dataagent\/skills\/[^/]+\/export$/)) {
    const folder = decodeURIComponent(pathname.split('/')[4])
    return createResponse(config, new Blob([`demo skill ${folder}`], { type: 'application/zip' }))
  }

  if (method === 'delete' && pathname.match(/^\/v1\/dataagent\/skills\/[^/]+$/)) {
    return createResponse(config, { status: 'ok' })
  }

  if (method === 'get' && pathname === '/v1/nl2sql/topics') {
    const list = params.agent_id
      ? demoTopics.filter((topic) => topic.agent_id === params.agent_id)
      : demoTopics
    return createResponse(config, clone(list.filter(topic => !topic.is_agent_preview).map(publicTopic)))
  }

  if (method === 'post' && pathname === '/v1/nl2sql/topics') {
    const topic = {
      topic_id: `demo-topic-${++demoTopicSeq}`,
      title: String(body.title || '新话题'),
      agent_id: String(body.agent_id || 'agent_default'),
      message_count: 0,
      current_task_id: '',
      current_task_status: '',
      created_at: demoNow(),
      updated_at: demoNow(),
      messages: []
    }
    demoTopics.unshift(topic)
    return createResponse(config, publicTopic(topic))
  }

  if (method === 'get' && pathname.match(/^\/v1\/nl2sql\/topics\/[^/]+$/)) {
    const topicId = decodeURIComponent(pathname.split('/').pop())
    const topic = findDemoTopic(topicId)
    return topic
      ? createResponse(config, clone(publicTopic(topic)))
      : createRejectedResponse(config, '话题不存在', 404)
  }

  if (method === 'put' && pathname.match(/^\/v1\/nl2sql\/topics\/[^/]+$/)) {
    const topicId = decodeURIComponent(pathname.split('/').pop())
    const topic = findDemoTopic(topicId)
    if (!topic) return createRejectedResponse(config, '话题不存在', 404)
    topic.title = String(body.title || topic.title)
    topic.updated_at = demoNow()
    return createResponse(config, clone(publicTopic(topic)))
  }

  if (method === 'delete' && pathname.match(/^\/v1\/nl2sql\/topics\/[^/]+$/)) {
    return createResponse(config, { status: 'ok' })
  }

  if (method === 'get' && pathname.match(/^\/v1\/nl2sql\/topics\/[^/]+\/messages$/)) {
    const topicId = decodeURIComponent(pathname.split('/')[4])
    const topic = findDemoTopic(topicId)
    const items = topic ? topic.messages : []
    return createResponse(config, {
      items: clone(items),
      total: items.length,
      page: Number(params.page || 1),
      page_size: Number(params.page_size || 500)
    })
  }

  if (method === 'put' && pathname.match(/^\/v1\/nl2sql\/topics\/[^/]+\/messages\/[^/]+\/feedback$/)) {
    const parts = pathname.split('/')
    const topicId = decodeURIComponent(parts[4])
    const messageId = decodeURIComponent(parts[6])
    const feedback = String(body.feedback || '').trim()
    if (!['', 'like', 'dislike'].includes(feedback)) {
      return createRejectedResponse(config, 'feedback must be like, dislike, or empty', 400)
    }
    const topic = findDemoTopic(topicId)
    const message = topic?.messages?.find((item) => String(item.message_id) === messageId)
    if (!message) return createRejectedResponse(config, '消息不存在', 404)
    if (String(message.sender_type || message.role || '') !== 'assistant') {
      return createRejectedResponse(config, '仅支持反馈 AI 消息', 400)
    }
    message.feedback = feedback
    topic.updated_at = demoNow()
    return createResponse(config, clone(message))
  }

  if (method === 'post' && pathname === '/v1/nl2sql/tasks/deliver-message') {
    const topic = findDemoTopic(body.topic_id)
    const now = demoNow()
    const taskId = `demo-task-${demoTaskSeq++}`
    const assistantMessageId = `demo-assistant-${demoTaskSeq}`
    if (topic) {
      topic.messages.push({
        message_id: `demo-user-${demoTaskSeq}`,
        sender_type: 'user',
        role: 'user',
        content: String(body.content || ''),
        created_at: now
      })
      topic.messages.push({
        message_id: assistantMessageId,
        sender_type: 'assistant',
        role: 'assistant',
        content: '',
        status: 'waiting',
        feedback: '',
        task_id: taskId,
        created_at: now
      })
      topic.current_task_id = taskId
      topic.current_task_status = 'waiting'
      topic.updated_at = now
    }
    demoTasks[taskId] = {
      task_id: taskId,
      topic_id: String(body.topic_id || ''),
      task_status: 'finished',
      assistant_message_id: assistantMessageId,
      created_at: now,
      updated_at: now
    }
    return createResponse(config, {
      accepted: true,
      task_id: taskId,
      assistant_message_id: assistantMessageId,
      task_status: 'waiting'
    })
  }

  if (method === 'get' && pathname.match(/^\/v1\/nl2sql\/tasks\/[^/]+$/)) {
    const taskId = decodeURIComponent(pathname.split('/').pop())
    return createResponse(config, clone(demoTasks[taskId] || {
      task_id: taskId,
      task_status: 'finished'
    }))
  }

  if (method === 'get' && pathname.match(/^\/v1\/nl2sql\/tasks\/[^/]+\/events$/)) {
    return createResponse(config, { items: [], total: 0 })
  }

  if (method === 'post' && pathname.match(/^\/v1\/nl2sql\/tasks\/[^/]+\/cancel$/)) {
    return createResponse(config, { status: 'ok' })
  }

  if (['post', 'put', 'delete', 'patch'].includes(method)) {
    return createRejectedResponse(config, '演示环境仅支持浏览与查询')
  }

  return createRejectedResponse(config, `未实现的演示接口：${method.toUpperCase()} ${pathname}`, 404)
}
