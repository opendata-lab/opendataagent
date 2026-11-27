/**
 * Unified timestamp parsing and formatting utilities.
 *
 * Backend APIs return RFC 3339 UTC strings (``YYYY-MM-DDTHH:MM:SSZ``).
 * Display functions format in the browser's local timezone by default, with an
 * optional ``timeZone`` override (IANA name) when a caller explicitly needs one.
 */

const DATE_ONLY_RE = /^(\d{4})-(\d{2})-(\d{2})$/

export function parseTimestamp(value) {
  if (value === null || value === undefined || value === '') return null
  if (value instanceof Date) {
    return Number.isNaN(value.getTime()) ? null : value
  }
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? null : date
}

function getDateParts(date, timeZone) {
  const formatter = new Intl.DateTimeFormat('en-CA', {
    ...(timeZone ? { timeZone } : {}),
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hourCycle: 'h23'
  })
  const parts = {}
  for (const part of formatter.formatToParts(date)) {
    if (part.type !== 'literal') {
      parts[part.type] = part.value
    }
  }
  return parts
}

function normalizeOptions(options, defaultFallback) {
  if (typeof options === 'string') {
    return { fallback: options, timeZone: undefined }
  }
  return {
    fallback: options?.fallback ?? defaultFallback,
    timeZone: options?.timeZone
  }
}

export function formatDateTime(value, options) {
  const { fallback, timeZone } = normalizeOptions(options, '—')
  const date = parseTimestamp(value)
  if (!date) return fallback
  const { year, month, day, hour, minute, second } = getDateParts(date, timeZone)
  return `${year}-${month}-${day} ${hour}:${minute}:${second}`
}

export function formatDate(value, options) {
  const { fallback, timeZone } = normalizeOptions(options, '—')
  const format = typeof options === 'object' && options?.format ? options.format : 'YYYY-MM-DD'
  const date = parseTimestamp(value)
  if (!date) return fallback
  const { year, month, day } = getDateParts(date, timeZone)
  return format === 'MM-DD' ? `${month}-${day}` : `${year}-${month}-${day}`
}

export function formatShortDateTime(value, options) {
  const { fallback, timeZone } = normalizeOptions(options, '—')
  const date = parseTimestamp(value)
  if (!date) return fallback
  const { month, day, hour, minute } = getDateParts(date, timeZone)
  return `${month}-${day} ${hour}:${minute}`
}

export function formatClockTime(value, options) {
  const { fallback, timeZone } = normalizeOptions(options, '')
  const date = parseTimestamp(value)
  if (!date) return fallback
  const { hour, minute } = getDateParts(date, timeZone)
  return `${hour}:${minute}`
}

export function formatRelativeTime(value, options = {}) {
  const date = parseTimestamp(value)
  if (!date) return ''
  const now = parseTimestamp(options?.now) || new Date()
  const timeZone = options?.timeZone

  const diffMs = now.getTime() - date.getTime()
  if (diffMs <= 0) return '刚刚'

  const d = getDateParts(date, timeZone)
  const n = getDateParts(now, timeZone)
  const diffDays = Math.round(
    (Date.UTC(Number(n.year), Number(n.month) - 1, Number(n.day)) -
      Date.UTC(Number(d.year), Number(d.month) - 1, Number(d.day))) /
      86400000
  )

  if (diffDays <= 0) {
    const diffSeconds = Math.floor(diffMs / 1000)
    const diffMinutes = Math.floor(diffSeconds / 60)
    if (diffSeconds < 60) return '刚刚'
    if (diffMinutes < 60) return `${diffMinutes}分钟前`
    return `${d.hour}:${d.minute}`
  }

  if (diffDays === 1) return '1天前'
  if (diffDays <= 7) return `${diffDays}天前`
  if (d.year === n.year) return `${d.month}-${d.day}`
  return `${d.year}-${d.month}-${d.day}`
}

/**
 * Convert a local calendar date (or inclusive [startDate, endDate] range of
 * ``YYYY-MM-DD`` strings) into a half-open UTC ISO range ``[start, end)``
 * spanning local midnight on the start date to local midnight on the day after
 * the end date.
 */
export function localDayRange(startDateStr, endDateStr = startDateStr) {
  const startMatch = DATE_ONLY_RE.exec(String(startDateStr || '').trim())
  const endMatch = DATE_ONLY_RE.exec(String(endDateStr || '').trim())
  if (!startMatch || !endMatch) return null
  const start = new Date(Number(startMatch[1]), Number(startMatch[2]) - 1, Number(startMatch[3]), 0, 0, 0, 0)
  const end = new Date(Number(endMatch[1]), Number(endMatch[2]) - 1, Number(endMatch[3]) + 1, 0, 0, 0, 0)
  if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime())) return null
  return {
    start: start.toISOString(),
    end: end.toISOString()
  }
}
