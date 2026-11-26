<template>
  <div class="dac-queue" part="queue">
    <div class="dac-queue-head">
      <span>待发送 {{ items.length }} 条</span>
      <span v-if="paused" class="dac-queue-paused">上一轮未完成，已暂停自动发送</span>
    </div>
    <div v-for="(item, index) in items" :key="item.id" class="dac-queue-item" part="queue-item">
      <span class="dac-queue-index">{{ index + 1 }}</span>
      <span class="dac-queue-text" :title="item.content">{{ item.content || '（仅附件）' }}</span>
      <span v-if="item.attachments.length" class="dac-queue-files">{{ item.attachments.length }} 个附件</span>
      <button
        type="button"
        class="dac-queue-btn is-primary"
        part="queue-send-now"
        :disabled="disabled"
        :title="active ? '停止当前运行并立即发送' : '立即发送'"
        @click="$emit('send-now', item.id)"
      >立即</button>
      <button type="button" class="dac-queue-btn" part="queue-edit" :disabled="disabled" title="放回输入框编辑" @click="$emit('edit', item.id)">编辑</button>
      <button type="button" class="dac-queue-btn" part="queue-remove" :disabled="disabled" title="删除" @click="$emit('remove', item.id)">删除</button>
    </div>
  </div>
</template>

<script setup>
defineProps({
  items: { type: Array, required: true },
  /** A run is in progress: 立即 stops it first. */
  active: { type: Boolean, default: false },
  /** The last run did not finish, so the queue waits for the user. */
  paused: { type: Boolean, default: false },
  disabled: { type: Boolean, default: false },
})
defineEmits(['send-now', 'edit', 'remove'])
</script>

<style>
/* A tray docked on the input card's top edge: slightly inset, no bottom
   border, so it reads as part of the input rather than a separate card. */
.dac-queue {
  margin: 0 12px;
  padding: 6px 10px 4px;
  border: 1px solid var(--dac-border, #e5e7eb);
  border-bottom: none;
  border-radius: 8px 8px 0 0;
  background: var(--dac-surface-muted, #f8fafc);
  font-size: 13px;
}
.dac-queue-head {
  display: flex;
  gap: 8px;
  align-items: center;
  margin-bottom: 4px;
  color: #606878;
  font-size: 12px;
}
.dac-queue-paused { color: #b45309; }
.dac-queue-item {
  display: flex;
  gap: 8px;
  align-items: center;
  padding: 4px 0;
}
.dac-queue-index { color: #9aa1ad; min-width: 14px; }
.dac-queue-text {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
  color: #1f2937;
}
.dac-queue-files { color: #606878; font-size: 12px; flex-shrink: 0; }
.dac-queue-btn {
  flex-shrink: 0;
  padding: 2px 8px;
  border: 1px solid var(--dac-border, #e5e7eb);
  border-radius: 6px;
  background: #fff;
  color: #374151;
  font-size: 12px;
  cursor: pointer;
}
.dac-queue-btn.is-primary {
  border-color: var(--dac-primary, #10b981);
  color: var(--dac-primary-dark, #059669);
}
.dac-queue-btn:disabled { opacity: 0.5; cursor: default; }
</style>
