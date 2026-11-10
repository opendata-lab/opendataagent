import { describe, expect, it } from 'vitest'
import fs from 'node:fs'
import path from 'node:path'

describe('DataAgent brand icon assets and index.html references', () => {
  const frontendDir = path.resolve(__dirname, '../..')
  const publicDir = path.resolve(frontendDir, 'public')
  const indexHtmlPath = path.resolve(frontendDir, 'index.html')

  it('provides all canonical dataagent-icon assets in public directory', () => {
    const expectedAssets = [
      'dataagent-icon.svg',
      'dataagent-icon.ico',
      'dataagent-icon-32.png',
      'dataagent-icon-64.png',
      'dataagent-icon-192.png',
      'dataagent-icon-512.png',
      'dataagent-icon-light.svg',
      'dataagent-icon-dark.svg',
      'dataagent-icon-light-fav.svg',
      'dataagent-icon-dark-fav.svg',
      'dataagent-icon-light-32.png',
      'dataagent-icon-light-64.png',
      'dataagent-icon-light-192.png',
      'dataagent-icon-light-512.png',
      'dataagent-icon-dark-32.png',
      'dataagent-icon-dark-64.png',
      'dataagent-icon-dark-192.png',
      'dataagent-icon-dark-512.png'
    ]

    for (const asset of expectedAssets) {
      const fullPath = path.join(publicDir, asset)
      expect(fs.existsSync(fullPath), `Expected ${asset} to exist in public/`).toBe(true)
      const stats = fs.statSync(fullPath)
      expect(stats.size).toBeGreaterThan(0)
    }
  })

  it('does not contain leftover legacy opendataworks or example icon assets', () => {
    const legacyAssets = [
      'opendataworks-icon.svg',
      'opendataworks-icon.ico',
      'opendataworks-icon-192.png',
      'example-icon.svg',
      'example-icon.ico',
      'example-icon-192.png'
    ]

    for (const asset of legacyAssets) {
      const fullPath = path.join(publicDir, asset)
      expect(fs.existsSync(fullPath), `Expected legacy asset ${asset} to be removed from public/`).toBe(false)
    }
  })

  it('references existing dataagent icons in index.html', () => {
    const html = fs.readFileSync(indexHtmlPath, 'utf-8')
    const iconHrefs = [...html.matchAll(/<link\s+[^>]*href="([^"]+)"[^>]*>/gi)]
      .map((match) => match[1])
      .filter((href) => href.endsWith('.svg') || href.endsWith('.ico') || href.endsWith('.png'))

    expect(iconHrefs.length).toBeGreaterThan(0)

    for (const href of iconHrefs) {
      expect(href).toMatch(/^\/dataagent-icon/)
      const relativeFile = href.replace(/^\//, '')
      const fullPath = path.join(publicDir, relativeFile)
      expect(fs.existsSync(fullPath), `index.html references ${href} which must exist in public/`).toBe(true)
    }
  })
})
