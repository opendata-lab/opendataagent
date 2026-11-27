<template>
  <el-drawer :model-value="modelValue" :title="drawerTitle" size="min(560px, 100vw)" destroy-on-close @update:model-value="$emit('update:modelValue', $event)">
    <template v-if="editing">
      <ExperienceFields ref="fieldsRef" v-model="form" :agents="agents" />
    </template>
    <template v-else-if="item">
      <p class="muted">{{ item.kind === 'knowledge' ? '知识' : '记忆' }} · {{ scopeName }} · {{ item.status === 'active' ? '生效' : item.status === 'pending' ? '待审核' : '已停用' }}</p>
      <h3>适用条件</h3><p class="read-text">{{ item.when_text || '尚未填写' }}</p>
      <h3>内容</h3><p class="read-text">{{ item.body }}</p>
      <h3>设置</h3>
      <dl>
        <dt>注入方式</dt><dd>{{ item.inject_mode === 'always' ? '始终注入 · 每次任务带上全文' : '按需读取 · 相关时再读全文' }}</dd>
        <dt>作用于</dt><dd>{{ scopeName }}</dd>
        <dt>最近修改</dt><dd>{{ item.updated_by }} · {{ formatTime(item.updated_at) }}</dd>
      </dl>
    </template>
    <ExperienceSources v-if="item" :item="item" :digest-agent-id="digestAgentId" />
    <template #footer>
      <div class="drawer-actions">
        <template v-if="editing">
          <el-button :disabled="saving" @click="$emit('update:modelValue', false)">取消</el-button>
          <el-button type="primary" :loading="saving" @click="save">保存</el-button>
        </template>
        <template v-else>
          <el-button v-if="!readonly && item" link type="danger" @click="$emit('delete', item)">删除</el-button>
          <el-button v-if="!readonly && item" @click="$emit('change', { item, changes: { status: item.status === 'active' ? 'disabled' : 'active' } })">{{ item.status === 'active' ? '停用' : '恢复' }}</el-button>
          <el-button v-if="!readonly && item" type="primary" @click="$emit('edit')">编辑</el-button>
          <el-button v-else @click="$emit('update:modelValue', false)">关闭</el-button>
        </template>
      </div>
    </template>
  </el-drawer>
</template>
<script setup>
import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { dataagentApi } from '@/api/dataagent'
import { formatDateTime } from '@/utils/time'
import ExperienceFields from './ExperienceFields.vue'
import ExperienceSources from './ExperienceSources.vue'
import { entryForm, entryPayload } from './entryForm'
const props = defineProps({ modelValue: Boolean, readonly: Boolean, item: { type: Object, default: null }, editing: Boolean,
  agents: { type: Array, default: () => [] }, digestAgentId: { type: String, required: true } })
const emit = defineEmits(['update:modelValue', 'saved', 'change', 'delete', 'edit'])
const fieldsRef = ref(), saving = ref(false), form = ref(entryForm(null))
const drawerTitle = computed(() => props.editing ? (props.item ? `编辑${props.item.kind === 'knowledge' ? '知识' : '记忆'}` : '新增知识') : props.item?.title || '')
const scopeName = computed(() => props.item?.scope === 'global' ? '全局（所有助手）' : props.agents.find(a => a.agent_id === props.item?.agent_id)?.name || props.item?.agent_id)
const formatTime = value => formatDateTime(value, '')
watch(() => [props.modelValue, props.item, props.editing], () => { if (props.modelValue) form.value = entryForm(props.item) })
async function save() {
  if (saving.value || !await fieldsRef.value.validate()) return
  saving.value = true
  try {
    const payload = entryPayload(form.value)
    if (props.item) await dataagentApi.updateExperience(props.item.experience_id, payload)
    else await dataagentApi.createExperience({ ...payload, kind: 'knowledge' })
    emit('update:modelValue', false)
    emit('saved')
    ElMessage.success('已保存，下一次任务开始使用')
  } catch (error) { if (!error?.__odwNotified) ElMessage.error(error?.message || '保存失败，请重试') }
  finally { saving.value = false }
}
</script>
<style scoped>
h3 { margin: 24px 0 8px; font-size: 14px; }
.read-text { line-height: 1.7; white-space: pre-wrap; overflow-wrap: anywhere; }
.muted, dt { color: #64748b; }
dl { display: grid; grid-template-columns: 80px minmax(0, 1fr); gap: 12px; font-size: 13px; }
dd { margin: 0; overflow-wrap: anywhere; }
.drawer-actions { display: flex; justify-content: flex-end; gap: 8px; }
</style>
