import { normalizePanelMode } from './panelLayout'

const DEFAULT_PROJECT_NAME = '智能问数'
const DEFAULT_PROJECT_COLOR = '#4A90A4'
const DEFAULT_POSITION = 'bottom-right'
const DEFAULT_DISPLAY_MODE = 'floating'

const trimTrailingSlash = (value) => String(value || '').replace(/\/+$/, '')

const randomId = () => {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID().replace(/-/g, '').slice(0, 24)
  }
  return `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 14)}`
}

const visitorStorageKey = (websiteId) => `example_widget_visitor_id_${websiteId || 'default'}`

export const parseBooleanFlag = (value) => value === true || String(value || '').trim().toLowerCase() === 'true'

const VALID_PERMISSION_MODES = new Set(['default', 'acceptEdits', 'plan', 'bypassPermissions'])
export function normalizePermissionMode(value) {
  const mode = String(value || '').trim()
  return VALID_PERMISSION_MODES.has(mode) ? mode : 'default'
}

// The smallest panel the widget lays out (`useWidgetGeometry` MIN_WIDTH).
const MIN_PANEL_WIDTH = 360

/** A configured panel width in whole pixels, at least the minimum; null when unset or invalid. */
export function normalizePanelWidth(value) {
  if (value == null || String(value).trim() === '') return null
  const width = Math.round(Number(value))
  if (!Number.isFinite(width) || width <= 0) return null
  return Math.max(MIN_PANEL_WIDTH, width)
}

export function resolveFullscreenUrl(value) {
  const url = String(value || '').trim()
  if (!url) return ''
  try {
    const resolved = new URL(url, window.location.href)
    return ['http:', 'https:'].includes(resolved.protocol) ? resolved.href : ''
  } catch {
    return ''
  }
}

export function resolveCurrentScript() {
  if (typeof document === 'undefined') return null
  return document.currentScript || document.querySelector('script[data-website-id][src*="dataagent-widget"]')
}

export function resolveVisitorId(websiteId) {
  const key = visitorStorageKey(websiteId)
  try {
    const existing = window.localStorage.getItem(key)
    if (existing) return existing
    const created = `visitor_${randomId()}`
    window.localStorage.setItem(key, created)
    return created
  } catch (_error) {
    return `visitor_${randomId()}`
  }
}

export function parseWidgetConfig(script = resolveCurrentScript()) {
  const dataset = script?.dataset || {}
  const websiteId = String(dataset.websiteId || '').trim()
  const userId = String(dataset.userId || '').trim()
  const agentId = String(dataset.agentId || '').trim()
  const allowAnonymous = parseBooleanFlag(dataset.allowAnonymous)
  const visitorId = userId || !allowAnonymous ? '' : resolveVisitorId(websiteId)
  const loginUrl = String(dataset.loginUrl || '').trim()
  const apiBaseUrl = trimTrailingSlash(dataset.apiBaseUrl || (script?.src ? new URL(script.src, window.location.href).origin : ''))
  const projectName = String(dataset.projectName || DEFAULT_PROJECT_NAME).trim() || DEFAULT_PROJECT_NAME
  const projectColor = String(dataset.projectColor || DEFAULT_PROJECT_COLOR).trim() || DEFAULT_PROJECT_COLOR
  const position = String(dataset.position || DEFAULT_POSITION).trim() || DEFAULT_POSITION
  const rawDisplayMode = String(dataset.displayMode || DEFAULT_DISPLAY_MODE).trim().toLowerCase()
  const displayMode = rawDisplayMode === 'inline' ? 'inline' : 'floating'
  const containerId = String(dataset.containerId || '').trim()
  const headers = {
    'X-DataAgent-Client': 'widget',
    'X-DataAgent-Website-Id': websiteId
  }
  if (userId) {
    headers['X-DataAgent-User-Id'] = userId
  } else if (visitorId) {
    headers['X-DataAgent-Visitor-Id'] = visitorId
  }

  return {
    websiteId,
    userId,
    agentId,
    visitorId,
    allowAnonymous,
    loginUrl,
    projectName,
    projectColor,
    apiBaseUrl,
    position,
    displayMode,
    panelMode: normalizePanelMode(dataset.panelMode),
    panelWidth: normalizePanelWidth(dataset.panelWidth),
    dockWidth: normalizePanelWidth(dataset.dockWidth),
    containerId,
    permissionMode: normalizePermissionMode(dataset.permissionMode),
    fullscreenUrl: resolveFullscreenUrl(dataset.fullscreenUrl),
    headers
  }
}

export function isWidgetLoginRequired(config = {}) {
  const websiteId = String(config.websiteId || config.headers?.['X-DataAgent-Website-Id'] || '').trim()
  if (!websiteId) return false
  const userId = String(config.userId || config.headers?.['X-DataAgent-User-Id'] || '').trim()
  return !userId && config.allowAnonymous !== true
}
