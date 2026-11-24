import { createRouter, createWebHistory } from 'vue-router'
import { normalizeAgentId, withAgentContext } from './agentContext'

// Legacy `?tab=` values mapped onto the canonical, user-facing page paths.
const LEGACY_TAB_TO_PATH = {
  chat: '/chat',
  'chat-v2': '/chat',
  skills: '/skills',
  agents: '/agents',
  models: '/models',
  mcp: '/mcp',
  widget: '/widget-access'
}

const withoutLegacyTab = (query = {}) => {
  const { tab: _omitTab, ...rest } = query
  return rest
}

export const redirectLegacyTab = (to) => {
  const rawTab = Array.isArray(to.query.tab) ? to.query.tab[0] : to.query.tab
  const tab = String(rawTab || '')
  return {
    path: LEGACY_TAB_TO_PATH[tab] || '/chat',
    query: withoutLegacyTab(to.query),
    hash: to.hash
  }
}

const legacyPathSegments = (rawPathMatch) => {
  if (Array.isArray(rawPathMatch)) {
    return rawPathMatch.map((segment) => String(segment)).filter(Boolean)
  }
  return String(rawPathMatch || '').split('/').filter(Boolean)
}

export const redirectLegacyIntelligentQueryPath = (to) => {
  const segments = legacyPathSegments(to.params.pathMatch)
  const query = withoutLegacyTab(to.query)
  const common = { query, hash: to.hash }

  if (segments.length === 1) {
    const routeNames = {
      chat: 'IntelligentQueryChat',
      skills: 'IntelligentQuerySkills',
      agents: 'IntelligentQueryAgents',
      models: 'IntelligentQueryModels',
      mcp: 'IntelligentQueryMcp',
      widget: 'IntelligentQueryWidget'
    }
    const name = routeNames[segments[0]]
    if (name) return { name, params: {}, ...common }
  }

  if (segments.length === 2 && segments[0] === 'skills') {
    return {
      name: 'IntelligentQuerySkillDetail',
      params: { folder: segments[1] },
      ...common
    }
  }

  if (segments.length === 2 && segments[0] === 'agents') {
    return {
      name: 'IntelligentQueryAgentDetail',
      params: { agentId: segments[1] },
      ...common
    }
  }

  return { path: '/chat', ...common }
}

export const routes = [
  {
    path: '/login',
    name: 'Login',
    component: () => import('@/views/LoginView.vue'),
    meta: { public: true, title: '登录' }
  },
  {
    path: '/',
    component: () => import('@/views/intelligence/IntelligentQueryView.vue'),
    children: [
      {
        path: '',
        redirect: (to) => ({ path: '/chat', query: to.query, hash: to.hash })
      },
      {
        path: 'chat',
        name: 'IntelligentQueryChat',
        component: () => import('@/views/intelligence/NL2SqlChatV2.vue'),
        meta: { tab: 'chat-v2', title: '智能问数', permission: 'menu:chat' }
      },
      {
        path: 'feedback',
        name: 'UserFeedback',
        component: () => import('@/views/settings/FeedbackView.vue'),
        meta: { tab: 'feedback', title: '用户反馈', permission: 'feedback:view' }
      },
      {
        path: 'knowledge',
        name: 'KnowledgeLibrary',
        component: () => import('@/views/settings/KnowledgeMemoryView.vue'),
        props: { kind: 'knowledge' },
        meta: { tab: 'knowledge', title: '知识', permission: 'experience:manage' }
      },
      {
        path: 'memories',
        name: 'MemoryLibrary',
        component: () => import('@/views/settings/KnowledgeMemoryView.vue'),
        props: { kind: 'memory' },
        meta: { tab: 'memories', title: '记忆', permission: 'experience:manage' }
      },
      {
        path: 'agents',
        name: 'IntelligentQueryAgents',
        component: () => import('@/views/intelligence/AgentStudio.vue'),
        meta: { tab: 'agents', title: '智能体', permission: 'menu:agents' }
      },
      {
        path: 'agents/:agentId',
        name: 'IntelligentQueryAgentDetail',
        component: () => import('@/views/intelligence/AgentDetailView.vue'),
        meta: { tab: 'agents', title: '智能体工作台', agentWorkbench: true, permission: 'menu:agents' }
      }
    ]
  },
  {
    path: '/settings',
    component: () => import('@/views/settings/SettingsLayout.vue'),
    children: [
      {
        path: '',
        redirect: '/settings/skills'
      },
      {
        path: 'skills',
        name: 'IntelligentQuerySkills',
        component: () => import('@/views/settings/SkillStudio.vue'),
        meta: { tab: 'skills', title: 'Skills', permission: 'menu:skills' }
      },
      {
        path: 'skills/:folder',
        name: 'IntelligentQuerySkillDetail',
        component: () => import('@/views/settings/SkillDetailView.vue'),
        meta: { tab: 'skills', title: 'Skill 详情', permission: 'menu:skills' }
      },
      {
        path: 'mcp',
        name: 'IntelligentQueryMcp',
        component: () => import('@/views/settings/McpConfig.vue'),
        // 与 skills 同样对登录用户开放只读视图；写操作由页面内 canManage 与
        // 后端权限依赖双重收口，非 admin 读到的凭证字段已脱敏。
        meta: { tab: 'mcp', title: 'MCP 服务', permission: 'menu:mcp' }
      },
      {
        path: 'mcp-tokens',
        name: 'McpTokens',
        component: () => import('@/views/settings/McpTokensView.vue'),
        meta: { tab: 'mcp-tokens', title: '访问令牌', permission: 'menu:mcp_tokens' }
      },
      {
        path: 'mcp-tokens/new',
        name: 'McpTokenCreate',
        component: () => import('@/views/settings/McpTokenCreateView.vue'),
        props: (route) => ({ configAgentId: normalizeAgentId(route.query.agent_id) }),
        meta: { tab: 'mcp-tokens', title: '新建访问令牌', permission: 'menu:mcp_tokens' }
      },
      {
        path: 'profile',
        name: 'UserProfile',
        component: () => import('@/views/settings/ProfileView.vue'),
        meta: { tab: 'profile', title: '个人资料', permission: 'menu:profile' }
      },
      {
        path: 'users',
        name: 'UserManagement',
        component: () => import('@/views/settings/UsersView.vue'),
        meta: { tab: 'users', title: '用户管理', permission: 'users:manage' }
      },
      {
        path: 'roles',
        name: 'RoleManagement',
        component: () => import('@/views/settings/RolesView.vue'),
        meta: { tab: 'roles', title: '角色管理', permission: 'roles:manage' }
      },
      {
        path: 'models',
        name: 'IntelligentQueryModels',
        component: () => import('@/views/settings/DataAgentConfig.vue'),
        meta: { tab: 'models', title: '模型管理', permission: 'models:manage' }
      },
      {
        path: 'widget-access',
        name: 'IntelligentQueryWidget',
        component: () => import('@/views/settings/WidgetAccessConfig.vue'),
        meta: { tab: 'widget', title: 'Widget 接入', permission: 'widget:manage' }
      },
      {
        path: 'evaluations',
        name: 'EvaluationSets',
        component: () => import('@/views/evaluation/EvaluationSetsView.vue'),
        meta: { tab: 'evaluations', title: '评测集', permission: 'evals:manage' }
      },
      {
        path: 'evaluations/:datasetId',
        name: 'EvaluationSetDetail',
        component: () => import('@/views/evaluation/EvaluationSetDetailView.vue'),
        meta: { tab: 'evaluations', title: '评测集详情', permission: 'evals:manage' }
      },
      {
        path: 'evaluation-results',
        name: 'EvaluationResults',
        component: () => import('@/views/evaluation/EvaluationResultsView.vue'),
        meta: { tab: 'eval-results', title: '评测结果', permission: 'evals:manage' }
      },
      {
        path: 'evaluation-results/:runId',
        name: 'EvaluationRunDetail',
        component: () => import('@/views/evaluation/EvaluationRunDetailView.vue'),
        meta: { tab: 'eval-results', title: '评测运行详情', permission: 'evals:manage' }
      }
    ]
  },
  // Settings moved under /settings; keep the old paths working so links and
  // bookmarks from before the move still land somewhere sensible.
  { path: '/skills', redirect: '/settings/skills' },
  { path: '/skills/:folder', redirect: (to) => `/settings/skills/${to.params.folder}` },
  { path: '/mcp', redirect: '/settings/mcp' },
  { path: '/models', redirect: '/settings/models' },
  { path: '/widget-access', redirect: '/settings/widget-access' },
  { path: '/evaluations', redirect: '/settings/evaluations' },
  { path: '/evaluation-results', redirect: '/settings/evaluation-results' },
  {
    path: '/intelligent-query',
    redirect: redirectLegacyTab
  },
  {
    path: '/intelligent-query/:pathMatch(.*)*',
    redirect: redirectLegacyIntelligentQueryPath
  },
  {
    path: '/nl2sql',
    // Keep old bookmarks usable while removing this implementation term from
    // the final browser URL.
    redirect: redirectLegacyTab
  }
]

const router = createRouter({
  // Keep the router base in sync with the Vite base so the app works under the
  // production `/dataagent/` prefix as well as any overridden mount point.
  history: createWebHistory(import.meta.env.BASE_URL),
  routes
})

// 统一账号认证与静态权限守卫
router.beforeEach(async (to) => {
  // 懒加载避免 router → store → api 的启动期循环依赖。
  const { sanitizeRedirectPath, useAuthStore } = await import('@/stores/auth')
  const authStore = useAuthStore()
  await authStore.bootstrap()

  if (to.meta?.public) {
    // 已登录再访问 /login：直接回应用。
    if (to.name === 'Login' && authStore.currentUser) {
      const fallback = withAgentContext({ path: '/chat' }, to.query)
      return sanitizeRedirectPath(to.query.redirect, fallback.query?.agent_id
        ? `/chat?agent_id=${encodeURIComponent(fallback.query.agent_id)}`
        : '/chat')
    }
    return true
  }
  if (!authStore.currentUser) {
    return { path: '/login', query: { redirect: to.fullPath } }
  }
  const requiredPermission = to.meta?.permission
  if (requiredPermission && !authStore.hasPermission(requiredPermission)) {
    if (to.path === '/chat') return true
    return withAgentContext({ path: '/chat' }, to.query)
  }
  return true
})

export default router
