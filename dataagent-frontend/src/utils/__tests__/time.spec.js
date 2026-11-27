import { describe, expect, it } from 'vitest'
import {
  formatClockTime,
  formatDate,
  formatDateTime,
  formatRelativeTime,
  formatShortDateTime,
  localDayRange,
  parseTimestamp
} from '../time'
import { compareTopicsByRecency } from '@/views/intelligence/topicHelpers'

describe('utils/time', () => {
  it('parses RFC 3339 Z timestamps and rejects empty/invalid inputs', () => {
    expect(parseTimestamp(null)).toBeNull()
    expect(parseTimestamp('')).toBeNull()
    expect(parseTimestamp('not-a-date')).toBeNull()
    const d = parseTimestamp('2026-11-26T09:15:30Z')
    expect(d).toBeInstanceOf(Date)
    expect(d.toISOString()).toBe('2026-11-26T09:15:30.000Z')
  })

  it('formats in browser local timezone by default and respects explicit timeZone override', () => {
    const iso = '2026-11-26T09:15:30Z'
    // Explicit overrides are deterministic regardless of runner TZ.
    expect(formatDateTime(iso, { timeZone: 'Asia/Shanghai' })).toBe('2026-11-26 17:15:30')
    expect(formatDateTime(iso, { timeZone: 'America/Los_Angeles' })).toBe('2026-11-26 01:15:30')
    expect(formatDate(iso, { timeZone: 'Asia/Shanghai' })).toBe('2026-11-26')
    expect(formatDate(iso, { format: 'MM-DD', timeZone: 'Asia/Shanghai' })).toBe('11-26')
    expect(formatShortDateTime(iso, { timeZone: 'Asia/Shanghai' })).toBe('11-26 17:15')
    expect(formatClockTime(iso, { timeZone: 'Asia/Shanghai' })).toBe('17:15')

    // Midnight formatting uses 00:05 (h23), never 24:05.
    expect(formatDateTime('2026-11-25T16:05:00Z', { timeZone: 'Asia/Shanghai' })).toBe('2026-11-26 00:05:00')

    // Default uses the process/browser local timezone (when TZ=America/Los_Angeles -> 01:15:30).
    if (Intl.DateTimeFormat().resolvedOptions().timeZone === 'America/Los_Angeles') {
      expect(formatDateTime(iso)).toBe('2026-11-26 01:15:30')
    }

    expect(formatDateTime(null)).toBe('—')
    expect(formatDateTime(null, '-')).toBe('-')
  })

  it('computes formatRelativeTime by calendar days and clamps future skew to 刚刚', () => {
    const tz = 'America/Los_Angeles'

    // Future timestamp (client clock slightly behind server) never produces "-1天前".
    expect(
      formatRelativeTime('2026-11-26T10:05:00Z', { now: '2026-11-26T10:00:00Z', timeZone: tz })
    ).toBe('刚刚')

    // Same day < 60s -> 刚刚; < 60m -> N分钟前; >= 60m -> HH:mm.
    expect(
      formatRelativeTime('2026-11-26T18:00:00Z', { now: '2026-11-26T18:00:30Z', timeZone: tz })
    ).toBe('刚刚')
    expect(
      formatRelativeTime('2026-11-26T18:00:00Z', { now: '2026-11-26T18:15:00Z', timeZone: tz })
    ).toBe('15分钟前')
    expect(
      formatRelativeTime('2026-11-26T18:00:00Z', { now: '2026-11-26T20:30:00Z', timeZone: tz })
    ).toBe('10:00')

    // Crossing midnight (< 24h elapsed: 23:30 PST -> 00:30 PST next day) shows 1天前, not 0天前.
    expect(
      formatRelativeTime('2026-11-27T07:30:00Z', { now: '2026-11-27T08:30:00Z', timeZone: tz })
    ).toBe('1天前')

    // 3 days ago -> 3天前; same year > 7 days -> MM-DD; previous year -> YYYY-MM-DD.
    expect(
      formatRelativeTime('2026-11-23T18:00:00Z', { now: '2026-11-26T18:00:00Z', timeZone: tz })
    ).toBe('3天前')
    expect(
      formatRelativeTime('2026-11-10T18:00:00Z', { now: '2026-11-26T18:00:00Z', timeZone: tz })
    ).toBe('11-10')
    expect(
      formatRelativeTime('2025-12-31T18:00:00Z', { now: '2026-11-26T18:00:00Z', timeZone: tz })
    ).toBe('2025-12-31')
  })

  it('builds half-open localDayRange instants in UTC ISO', () => {
    expect(localDayRange('')).toBeNull()
    expect(localDayRange('2026/11/26')).toBeNull()

    const single = localDayRange('2026-11-26')
    const expectedStart = new Date(2026, 10, 26, 0, 0, 0, 0).toISOString()
    const expectedEnd = new Date(2026, 10, 27, 0, 0, 0, 0).toISOString()
    expect(single).toEqual({ start: expectedStart, end: expectedEnd })

    if (Intl.DateTimeFormat().resolvedOptions().timeZone === 'America/Los_Angeles') {
      expect(single).toEqual({
        start: '2026-11-26T08:00:00.000Z',
        end: '2026-11-27T08:00:00.000Z'
      })
    }

    const multi = localDayRange('2026-11-24', '2026-11-26')
    expect(multi).toEqual({
      start: new Date(2026, 10, 24, 0, 0, 0, 0).toISOString(),
      end: new Date(2026, 10, 27, 0, 0, 0, 0).toISOString()
    })
  })

  it('sorts topics numerically by recency even with mixed sub-second precision', () => {
    const older = { topic_id: 'a', updated_at: '2026-11-26T09:00:00Z' }
    const newer = { topic_id: 'b', updated_at: '2026-11-26T09:00:00.123Z' }
    const oldest = { topic_id: 'c', created_at: '2026-11-26T08:59:59Z' }
    const sorted = [oldest, older, newer].sort(compareTopicsByRecency)
    expect(sorted.map((t) => t.topic_id)).toEqual(['b', 'a', 'c'])
  })
})
