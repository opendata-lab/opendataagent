import { beforeEach, describe, expect, it, vi } from 'vitest'

const authState = vi.hoisted(() => ({
  enabled: true,
  currentUser: null,
  isAdmin: false,
  bootstrap: vi.fn(async () => {})
}))

vi.mock('@/stores/auth', () => ({
  useAuthStore: () => authState,
  sanitizeRedirectPath: (raw, fallback = '/chat') => {
    const value = String(raw || '').trim()
    if (!value.startsWith('/') || value.startsWith('//') || value.includes('\\')) return fallback
    return value
  }
}))

vi.mock('@/views/LoginView.vue', () => ({ default: { template: '<div>login</div>' } }))
vi.mock('@/views/settings/McpTokensView.vue', () => ({ default: { template: '<div>mcp-tokens</div>' } }))
vi.mock('@/views/settings/ProfileView.vue', () => ({ default: { template: '<div>profile</div>' } }))
vi.mock('@/views/settings/UsersView.vue', () => ({ default: { template: '<div>users</div>' } }))
vi.mock('@/views/settings/RolesView.vue', () => ({ default: { template: '<div>roles</div>' } }))
vi.mock('@/views/intelligence/IntelligentQueryView.vue', () => ({ default: { template: '<div><router-view /></div>' } }))
vi.mock('@/views/intelligence/NL2SqlChatV2.vue', () => ({ default: { template: '<div>chat</div>' } }))
vi.mock('@/views/settings/SettingsLayout.vue', () => ({ default: { template: '<div><router-view /></div>' } }))
vi.mock('@/views/settings/SkillStudio.vue', () => ({ default: { template: '<div>skills</div>' } }))
vi.mock('@/views/settings/SkillDetailView.vue', () => ({ default: { template: '<div>skill</div>' } }))
vi.mock('@/views/intelligence/AgentStudio.vue', () => ({ default: { template: '<div>agents</div>' } }))
vi.mock('@/views/intelligence/AgentDetailView.vue', () => ({ default: { template: '<div>agent</div>' } }))
vi.mock('@/views/settings/DataAgentConfig.vue', () => ({ default: { template: '<div>models</div>' } }))
vi.mock('@/views/settings/WidgetAccessConfig.vue', () => ({ default: { template: '<div>widget</div>' } }))

import router from '../index'

let navSeq = 0
const navigate = async (location) => {
  const separator = String(location).includes('?') ? '&' : '?'
  await router.push(`${location}${separator}_t=${navSeq++}`).catch(() => {})
  await router.isReady()
  return router.currentRoute.value
}

const signInAsUser = () => {
  authState.currentUser = { id: 2, username: 'alice', display_name: 'alice' }
  authState.isAdmin = false
}

const signInAsAdmin = () => {
  authState.currentUser = { id: 1, username: 'admin', display_name: 'admin' }
  authState.isAdmin = true
}

describe('auth router guard', () => {
  beforeEach(() => {
    authState.enabled = true
    authState.currentUser = null
    authState.isAdmin = false
    authState.bootstrap.mockClear()
  })

  it('redirects unauthenticated users from /settings/mcp-tokens to /login and returns afterward', async () => {
    const login = await navigate('/settings/mcp-tokens')
    expect(login.path).toBe('/login')
    expect(login.query.redirect).toContain('/settings/mcp-tokens')
    signInAsUser()
    await router.push(login.query.redirect)
    expect(router.currentRoute.value.name).toBe('McpTokens')
  })

  it('redirects unauthenticated users to /login with redirect', async () => {
    const route = await navigate('/chat?agent_id=agent_sales')
    expect(route.path).toBe('/login')
    expect(route.query.redirect).toContain('/chat')
    expect(route.query.redirect).toContain('agent_id=agent_sales')
  })

  it('lets authenticated users through', async () => {
    signInAsUser()
    const route = await navigate('/chat')
    expect(route.path).toBe('/chat')
  })

  it.each(['/settings/models', '/settings/users', '/settings/roles', '/settings/widget-access', '/settings/evaluations', '/settings/evaluation-results', '/feedback', '/knowledge', '/memories'])(
    'blocks regular users from privileged route %s',
    async (path) => {
      signInAsUser()
      expect((await navigate(path)).path).toBe('/chat')
    }
  )

  it('keeps agent_id when a non-admin user falls back from an admin page', async () => {
    signInAsUser()
    const route = await navigate('/settings/models?agent_id=agent_sales')
    expect(route.path).toBe('/chat')
    expect(route.query.agent_id).toBe('agent_sales')
  })

  it.each([
    '/settings/skills',
    '/settings/skills/demo',
    '/settings/mcp-tokens',
    '/settings/profile',
    '/agents',
    '/agents/agent_1'
  ])('lets regular users access permitted route %s', async (path) => {
    signInAsUser()
    const route = await navigate(path)
    expect(route.path).toBe(path)
  })

  it('applies admin guard after a legacy route redirect', async () => {
    signInAsUser()
    const route = await navigate('/intelligent-query/models')
    expect(route.path).toBe('/chat')
  })

  it.each(['/settings/models', '/settings/users', '/settings/roles'])(
    'lets admins into admin route %s',
    async (path) => {
      signInAsAdmin()
      const route = await navigate(path)
      expect(route.path).toBe(path)
    }
  )

  it('bounces a logged-in user away from /login', async () => {
    signInAsUser()
    const route = await navigate('/login')
    expect(route.path).toBe('/chat')
  })

  it('returns a logged-in user to the full redirect including agent_id', async () => {
    signInAsUser()
    const route = await navigate('/login?redirect=%2Fsettings%2Fskills%3Fagent_id%3Dagent_sales')
    expect(route.path).toBe('/settings/skills')
    expect(route.query.agent_id).toBe('agent_sales')
  })
})
