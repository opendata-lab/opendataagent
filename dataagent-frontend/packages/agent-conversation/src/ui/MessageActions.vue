<template>
  <div class="dac-actions" part="message-actions">
    <time v-if="time" class="dac-time" part="message-time" :datetime="timestamp">{{ time }}</time>

    <button
      type="button"
      class="dac-action"
      part="copy-button"
      data-action="copy"
      :aria-label="copied ? '已复制' : '复制'"
      @click="copy"
    >{{ copied ? '已复制' : '复制' }}</button>

    <!-- Rating only exists when the host can store it. Showing the buttons
         without somewhere to send them produces a control that appears to work
         and silently discards the answer. -->
    <template v-if="canRate">
      <button
        type="button"
        class="dac-action"
        part="feedback-button"
        data-action="like"
        :class="{ 'is-on': message.feedback === 'like' }"
        :aria-pressed="message.feedback === 'like'"
        aria-label="有帮助"
        @click="$emit('feedback', { message, value: 'like' })"
      >
        <svg
          class="dac-thumb"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          stroke-width="2"
          stroke-linejoin="round"
          aria-hidden="true"
        >
          <path d="M7 11v10H4a2 2 0 0 1-2-2v-6a2 2 0 0 1 2-2h3Z" />
          <path d="M7 11 12 2a3 3 0 0 1 3 3v4h4a2 2 0 0 1 2 2l-1 8a2 2 0 0 1-2 2H7" />
        </svg>
      </button>
      <button
        type="button"
        class="dac-action"
        part="feedback-button"
        data-action="dislike"
        :class="{ 'is-on': message.feedback === 'dislike' }"
        :aria-pressed="message.feedback === 'dislike'"
        aria-label="没帮助"
        @click="$emit('feedback', { message, value: 'dislike' })"
      >
        <svg
          class="dac-thumb"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          stroke-width="2"
          stroke-linejoin="round"
          aria-hidden="true"
        >
          <path d="M17 13V3h3a2 2 0 0 1 2 2v6a2 2 0 0 1-2 2h-3Z" />
          <path d="M17 13 12 22a3 3 0 0 1-3-3v-4H5a2 2 0 0 1-2-2l1-8a2 2 0 0 1 2-2h11" />
        </svg>
      </button>
    </template>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { getMessageCopyText } from '../core/message.js'
import { copyText } from '../utils/clipboard.js'

const props = defineProps({
  message: { type: Object, required: true },
  canRate: { type: Boolean, default: false },
})

defineEmits(['feedback'])

const copied = ref(false)
let resetTimer = null

const timestamp = computed(() => {
  const message = props.message
  return (message.role === 'assistant' && message.finishedAt) || message.createdAt
})

const time = computed(() => {
  if (!timestamp.value) return ''
  const at = new Date(timestamp.value)
  if (Number.isNaN(at.getTime())) return ''
  return `${String(at.getHours()).padStart(2, '0')}:${String(at.getMinutes()).padStart(2, '0')}`
})

const copy = async () => {
  const text = getMessageCopyText(props.message)
  if (!text) return
  try {
    await copyText(text)
    copied.value = true
    if (resetTimer) clearTimeout(resetTimer)
    resetTimer = setTimeout(() => { copied.value = false }, 1500)
  } catch {
    // A denied clipboard is the browser's decision, not a conversation error;
    // the text stays selectable either way.
  }
}
</script>

<style>
.dac-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 20px;
  color: var(--dac-text-muted, #64748b);
  font-size: 12px;
  opacity: 0;
  transition: opacity 0.15s ease;
}
.dac-message:hover .dac-actions,
.dac-actions:focus-within { opacity: 1; }
.dac-action {
  padding: 2px 6px;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: inherit;
  font: inherit;
  cursor: pointer;
}
.dac-action:hover { background: var(--dac-assistant-bubble-bg, #f1f5f9); }
.dac-action.is-on { color: var(--dac-accent, #0f766e); }
.dac-thumb { display: block; width: 14px; height: 14px; }
.dac-action.is-on .dac-thumb { fill: currentColor; }
</style>
