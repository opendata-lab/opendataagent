import { defineStore } from 'pinia'
import { createNl2SqlApiClient, DATAAGENT_CLIENT_HEADERS } from '@/api/nl2sql'

const { authApi } = createNl2SqlApiClient({ defaultHeaders: DATAAGENT_CLIENT_HEADERS })

// 只接受同源应用内路径，防开放重定向（与后端 sanitize_redirect_path 同规则）。
export function sanitizeRedirectPath(raw, fallback = '/chat') {
  const value = String(raw || '').trim()
  if (!value.startsWith('/') || value.startsWith('//') || value.includes('\\')) {
    return fallback
  }
  return value
}

export const useAuthStore = defineStore('auth', {
  state: () => ({
    // 统一账号体系始终启用登录态；不再存在无认证降级。
    enabled: true,
    providerName: '',
    providerIcon: '',
    localLoginEnabled: true,
    oauthLoginEnabled: false,
    currentUser: null,
    bootstrapped: false
  }),

  getters: {
    isAuthenticated: (state) => Boolean(state.currentUser),
    roles: (state) => (Array.isArray(state.currentUser?.roles) ? state.currentUser.roles : []),
    isAdmin: (state) => state.currentUser?.is_admin === true
  },

  actions: {
    async bootstrap() {
      if (this.bootstrapped) return
      try {
        const config = await authApi.getAuthConfig()
        this.enabled = config?.enabled !== false
        this.providerName = String(config?.provider_name || '')
        this.providerIcon = String(config?.provider_icon || '')
        this.localLoginEnabled = config?.local_login_enabled !== false
        this.oauthLoginEnabled = Boolean(config?.oauth_login_enabled)
      } catch (_error) {
        // 后端不可达时 fail-closed，绝不放行管理界面。
        this.enabled = true
        this.currentUser = null
        this.bootstrapped = true
        return
      }
      try {
        this.currentUser = await authApi.me()
      } catch (_error) {
        this.currentUser = null
      }
      this.bootstrapped = true
    },

    async refreshMe() {
      try {
        this.currentUser = await authApi.me()
        return this.currentUser
      } catch (_error) {
        this.currentUser = null
        return null
      }
    },

    async loginLocal(username, password) {
      const user = await authApi.login(username, password)
      this.currentUser = user
      return user
    },

    async logout() {
      try {
        await authApi.logout()
      } finally {
        this.currentUser = null
      }
    },

    oauthAuthorizeUrl(redirect = '') {
      return authApi.oauthAuthorizeUrl(sanitizeRedirectPath(redirect, ''))
    }
  }
})
