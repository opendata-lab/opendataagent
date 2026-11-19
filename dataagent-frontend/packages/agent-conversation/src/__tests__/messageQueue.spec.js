import { describe, it, expect, vi, beforeAll } from 'vitest'
import { nextTick } from 'vue'
import { defineAgentConversation, DEFAULT_TAG } from '../element.js'
import { ConversationError } from '../transport/errors.js'

beforeAll(() => {
  defineAgentConversation()
})

const settle = async (ticks = 12) => {
  for (let i = 0; i < ticks; i += 1) await Promise.resolve()
  await new Promise((resolve) => setTimeout(resolve, 20))
  for (let i = 0; i < ticks; i += 1) await Promise.resolve()
  await nextTick()
}

/**
 * A transport whose runs stay open until the test ends them, so the element
 * is "running" while the test queues messages.
 */
const makeTransport = () => {
  const ends = new Map()
  let next = 0
  const transport = {
    loadConversation: vi.fn(async () => ({ messages: [], run: null })),
    sendMessage: vi.fn(async () => ({ taskId: `t-${++next}`, status: 'running', detail: '' })),
    cancelRun: vi.fn(async ({ taskId }) => {
      ends.get(taskId)?.('cancelled')
      return { taskId, status: 'cancelled', detail: '' }
    }),
    submitInteraction: vi.fn(async () => {}),
    fileUrl: (p) => `/files/${p}`,
    streamEvents: async function* ({ taskId }) {
      const status = await new Promise((resolve) => ends.set(taskId, resolve))
      yield { type: 'terminal', run: { taskId, status, detail: '' } }
    },
  }
  return { transport, end: (taskId, status = 'finished') => ends.get(taskId)?.(status) }
}

const mount = (transport) => {
  const el = document.createElement(DEFAULT_TAG)
  Object.assign(el, { endpoint: '/conv/q', transportFactory: () => transport })
  document.body.appendChild(el)
  return el
}

const type = async (el, text) => {
  el.value = text
  await settle()
}
const sendButton = (el) => el.shadowRoot.querySelector('[part~="send-button"]')
const queueTexts = (el) => [...el.shadowRoot.querySelectorAll('[part~="queue-item"] .dac-queue-text')].map((n) => n.textContent)
const sentContents = (transport) => transport.sendMessage.mock.calls.map(([request]) => request.content)

describe('message queue', () => {
  it('queues while a run is active and sends after it finishes', async () => {
    const { transport, end } = makeTransport()
    const el = mount(transport)
    await settle()

    await type(el, '第一条')
    sendButton(el).click()
    await settle()
    expect(sentContents(transport)).toEqual(['第一条'])

    await type(el, '第二条')
    expect(sendButton(el).getAttribute('title')).toBe('加入队列')
    sendButton(el).click()
    await settle()
    await type(el, '第三条')
    sendButton(el).click()
    await settle()

    expect(sentContents(transport)).toEqual(['第一条'])
    expect(queueTexts(el)).toEqual(['第二条', '第三条'])
    expect(el.value).toBe('')

    end('t-1')
    await settle()
    expect(sentContents(transport)).toEqual(['第一条', '第二条'])
    expect(queueTexts(el)).toEqual(['第三条'])

    end('t-2')
    await settle()
    expect(sentContents(transport)).toEqual(['第一条', '第二条', '第三条'])
    expect(queueTexts(el)).toEqual([])
    el.remove()
  })

  it('立即 stops the running task and sends that message next', async () => {
    const { transport } = makeTransport()
    const el = mount(transport)
    await settle()

    await type(el, '开始')
    sendButton(el).click()
    await settle()
    for (const text of ['甲', '乙']) {
      await type(el, text)
      sendButton(el).click()
      await settle()
    }

    const items = el.shadowRoot.querySelectorAll('[part~="queue-send-now"]')
    items[1].click()
    await settle()

    expect(transport.cancelRun).toHaveBeenCalledWith({ taskId: 't-1' })
    expect(sentContents(transport)).toEqual(['开始', '乙'])
    expect(queueTexts(el)).toEqual(['甲'])
    el.remove()
  })

  it('pauses after a failed run instead of sending into it', async () => {
    const { transport, end } = makeTransport()
    const el = mount(transport)
    await settle()

    await type(el, '开始')
    sendButton(el).click()
    await settle()
    await type(el, '后续')
    sendButton(el).click()
    await settle()

    end('t-1', 'failed')
    await settle()
    expect(sentContents(transport)).toEqual(['开始'])
    expect(el.shadowRoot.querySelector('.dac-queue-paused')).not.toBeNull()

    el.shadowRoot.querySelector('[part~="queue-send-now"]').click()
    await settle()
    expect(sentContents(transport)).toEqual(['开始', '后续'])
    el.remove()
  })

  it('edit puts a queued message back into the input; remove drops it', async () => {
    const { transport } = makeTransport()
    const el = mount(transport)
    await settle()

    await type(el, '开始')
    sendButton(el).click()
    await settle()
    for (const text of ['改这条', '删这条']) {
      await type(el, text)
      sendButton(el).click()
      await settle()
    }

    el.shadowRoot.querySelectorAll('[part~="queue-remove"]')[1].click()
    await settle()
    el.shadowRoot.querySelector('[part~="queue-edit"]').click()
    await settle()
    expect(queueTexts(el)).toEqual([])
    expect(el.value).toBe('改这条')
    el.remove()
  })

  it('retries a queued send while the host answers 409 busy, without reporting an error', async () => {
    const { transport, end } = makeTransport()
    const busy = new ConversationError('conversation_unavailable', '上一轮结果还在处理，请稍候', '', 409)
    const el = mount(transport)
    const errors = []
    el.addEventListener('dataagent-error', (event) => errors.push(event.detail))
    await settle()

    await type(el, '开始')
    sendButton(el).click()
    await settle()
    await type(el, '排队')
    sendButton(el).click()
    await settle()

    const original = transport.sendMessage.getMockImplementation()
    transport.sendMessage.mockImplementationOnce(async () => { throw busy })
    transport.sendMessage.mockImplementation(original)
    end('t-1')
    await settle()
    expect(sentContents(transport)).toEqual(['开始', '排队'])
    expect(queueTexts(el)).toEqual(['排队'])

    await new Promise((resolve) => setTimeout(resolve, 1100))
    await settle()
    expect(sentContents(transport)).toEqual(['开始', '排队', '排队'])
    expect(queueTexts(el)).toEqual([])
    expect(errors).toEqual([])
    el.remove()
  })
})
