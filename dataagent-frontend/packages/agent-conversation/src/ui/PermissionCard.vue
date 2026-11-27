<template>
  <!-- A plan is a document to approve, not a tool call: it gets its own card. -->
  <PlanCard v-if="isPlan" :block="block" :disabled="disabled" @decide="(payload) => emit('decide', { ...payload, plan: true })" />
  <div v-else class="v2-perm-card" :class="`risk-${effectiveRisk}`">
    <div class="v2-perm-head">
      <span class="v2-perm-badge">{{ riskLabel }}</span>
      <span class="v2-perm-title"><strong>{{ toolLabel }}</strong> 需要你确认</span>
    </div>
    <div v-if="block.summary && block.summary !== toolLabel && block.summary !== bareTool" class="v2-perm-summary">{{ block.summary }}</div>
    <!-- The title comes from the MCP server, so the real name stays visible on the approval gate. -->
    <div class="v2-perm-tool">工具：<template v-if="block.tool_title">{{ block.tool_title }}（<code>{{ bareTool }}</code>）</template><code v-else>{{ bareTool }}</code></div>
    <details v-if="hasPreview" class="v2-perm-preview">
      <summary>参数详情</summary>
      <pre>{{ prettyPreview }}</pre>
    </details>

    <template v-if="isPending">
      <div v-if="!denying" class="v2-perm-actions">
        <span v-if="block._submitFailed" class="v2-perm-failed-hint">提交失败，请重试</span>
        <button type="button" class="v2-perm-btn deny" :disabled="disabled || submitting" @click="startDeny">拒绝</button>
        <button
          v-if="canRememberSession"
          type="button"
          class="v2-perm-btn allow-session"
          :disabled="disabled || submitting"
          @click="decide('allow', { remember: 'session' })"
        >本会话内允许此工具</button>
        <button type="button" class="v2-perm-btn allow" :disabled="disabled || submitting" @click="decide('allow')">允许</button>
      </div>
      <div v-else class="v2-perm-deny-box">
        <textarea
          ref="denyInputRef"
          v-model="denyNote"
          class="v2-perm-deny-input"
          rows="2"
          :disabled="disabled || submitting"
          placeholder="告诉智能体换个做法（可选）"
          @keydown.enter.exact.prevent="confirmDeny"
        />
        <div class="v2-perm-actions">
          <span v-if="block._submitFailed" class="v2-perm-failed-hint">提交失败，请重试</span>
          <button type="button" class="v2-perm-btn cancel" :disabled="submitting" @click="cancelDeny">取消</button>
          <button type="button" class="v2-perm-btn deny-confirm" :disabled="disabled || submitting" @click="confirmDeny">确认拒绝</button>
        </div>
      </div>
    </template>
    <div v-else class="v2-perm-result" :class="block.decision">{{ resultLabel }}</div>
  </div>
</template>

<script setup>
import { computed, nextTick, ref, watch } from 'vue'

import PlanCard from './PlanCard.vue'

const props = defineProps({
  block: { type: Object, required: true },
  disabled: { type: Boolean, default: false },
})
const emit = defineEmits(['decide'])

const submitting = ref(false)
const denying = ref(false)
const denyNote = ref('')
const denyInputRef = ref(null)

watch(() => props.block._submitFailed, (failed) => {
  if (failed) submitting.value = false
})

const isPending = computed(() => (props.block.decision || 'pending') === 'pending')
const bareTool = computed(() => {
  const name = String(props.block.tool_name || '')
  return name.startsWith('mcp__') ? name.split('__').pop() : name
})
const toolLabel = computed(() => String(props.block.tool_title || '') || bareTool.value)
const isPlan = computed(() => props.block.risk_level === 'plan')
const isDestructive = computed(() => Boolean(props.block.destructive))
const canRememberSession = computed(() => !isDestructive.value)
const effectiveRisk = computed(() => (isDestructive.value || props.block.risk_level === 'critical' ? 'critical' : (props.block.risk_level || 'high')))
const riskLabel = computed(() => {
  if (effectiveRisk.value === 'critical') return '高危操作'
  return '写操作'
})
const hasPreview = computed(() => props.block.payload_preview != null && typeof props.block.payload_preview === 'object')
const prettyPreview = computed(() => {
  try {
    return JSON.stringify(props.block.payload_preview, null, 2)
  } catch {
    return String(props.block.payload_preview)
  }
})
const resultLabel = computed(() => {
  switch (props.block.decision) {
    case 'allow':
      return props.block.remember === 'session' ? '✓ 已允许执行（本会话内不再询问）' : '✓ 已允许执行'
    case 'deny':
      return props.block.note ? `✕ 已拒绝：${props.block.note}` : '✕ 已拒绝'
    case 'timeout':
      return '⏱ 等待确认超时，已自动拒绝'
    default:
      return ''
  }
})

function decide(decision, extra = {}) {
  if (props.disabled || submitting.value) return
  submitting.value = true
  const payload = { requestId: props.block.requestId, decision }
  if (extra.remember) payload.remember = extra.remember
  if (extra.note) payload.note = extra.note
  emit('decide', payload)
}

function startDeny() {
  if (props.disabled || submitting.value) return
  denying.value = true
  nextTick(() => denyInputRef.value?.focus())
}

function cancelDeny() {
  if (submitting.value) return
  denying.value = false
  denyNote.value = ''
}

function confirmDeny() {
  const note = denyNote.value.trim()
  decide('deny', note ? { note } : {})
}

</script>

<style scoped>
.v2-perm-card {
  border: 1px solid #f0c36d;
  border-radius: 10px;
  background: #fffaf0;
  padding: 12px 14px;
  margin: 8px 0;
}
.v2-perm-card.risk-critical {
  border-color: #e88;
  background: #fff5f5;
}
.v2-perm-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}
.v2-perm-badge {
  font-size: 12px;
  font-weight: 600;
  color: #fff;
  background: #e6a23c;
  border-radius: 4px;
  padding: 1px 8px;
}
.risk-critical .v2-perm-badge { background: #e05656; }
.v2-perm-title { font-weight: 500; font-size: 14px; }
.v2-perm-title strong { font-weight: 600; }
.v2-perm-summary { font-size: 13px; color: #555; margin: 4px 0; white-space: pre-wrap; }

.v2-perm-tool { font-size: 12px; color: #777; margin: 4px 0; }
.v2-perm-tool code { background: #eef1f6; padding: 1px 5px; border-radius: 4px; }
.v2-perm-preview { margin: 6px 0; }
.v2-perm-preview pre {
  max-height: 200px;
  overflow: auto;
  background: #f4f7fb;
  border-radius: 6px;
  padding: 8px;
  font-size: 12px;
  margin: 6px 0 0;
}
.v2-perm-actions { display: flex; flex-wrap: wrap; gap: 8px; justify-content: flex-end; margin-top: 8px; }
.v2-perm-btn {
  border: none;
  border-radius: 6px;
  padding: 6px 14px;
  font-size: 13px;
  cursor: pointer;
}
.v2-perm-btn:disabled { opacity: 0.5; cursor: not-allowed; }
.v2-perm-btn.allow { background: #2c9c5a; color: #fff; }
.v2-perm-btn.allow-session { background: #e7f6ed; color: #1e7a44; border: 1px solid #b8e3c9; }
.v2-perm-btn.deny, .v2-perm-btn.cancel { background: #eef1f6; color: #333; }
.v2-perm-btn.deny-confirm { background: #d9534f; color: #fff; }
.v2-perm-deny-box { margin-top: 8px; }
.v2-perm-deny-input {
  width: 100%;
  box-sizing: border-box;
  border: 1px solid #d5dbe5;
  border-radius: 6px;
  padding: 6px 8px;
  font-size: 13px;
  font-family: inherit;
  resize: vertical;
  background: #fff;
}
.v2-perm-result { margin-top: 6px; font-size: 13px; font-weight: 600; }
.v2-perm-result.allow { color: #2c9c5a; }
.v2-perm-result.deny, .v2-perm-result.timeout { color: #c0392b; }
.v2-perm-failed-hint { font-size: 12px; color: #c0392b; align-self: center; margin-right: auto; }
</style>
