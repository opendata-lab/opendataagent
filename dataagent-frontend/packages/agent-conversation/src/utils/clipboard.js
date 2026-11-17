const focusWithoutScroll = (el) => {
  if (!el || typeof el.focus !== 'function') return
  try {
    el.focus({ preventScroll: true })
  } catch {
    el.focus()
  }
}

const fallbackCopyText = (text) => {
  if (typeof document === 'undefined' || !document.body) {
    throw new Error('Clipboard copy is not available')
  }

  const active = document.activeElement instanceof HTMLElement ? document.activeElement : null
  const textarea = document.createElement('textarea')
  textarea.value = text
  textarea.setAttribute('readonly', '')
  textarea.style.position = 'fixed'
  textarea.style.top = '0'
  textarea.style.left = '0'
  textarea.style.width = '1px'
  textarea.style.height = '1px'
  textarea.style.opacity = '0'

  document.body.appendChild(textarea)
  let copied = false
  try {
    focusWithoutScroll(textarea)
    textarea.select()
    textarea.setSelectionRange(0, textarea.value.length)
    copied = typeof document.execCommand === 'function' && document.execCommand('copy')
  } finally {
    document.body.removeChild(textarea)
    focusWithoutScroll(active)
  }

  if (!copied) {
    throw new Error('Clipboard copy is not supported')
  }
}

export const copyText = async (value) => {
  const text = String(value ?? '')

  if (typeof navigator !== 'undefined' && navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text)
      return
    } catch (error) {
      fallbackCopyText(text)
      return
    }
  }

  fallbackCopyText(text)
}
