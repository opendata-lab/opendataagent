<template>
  <el-form :model="modelValue" label-position="top" class="entry-fields">
    <el-form-item label="标题" required :error="errors.title">
      <el-input v-model="modelValue.title" placeholder="一句话说明这条知识" />
      <div class="field-meta">
        <span class="word-count" :class="{ 'is-over': titleLength > 40 }">{{ titleLength }} / 40</span>
      </div>
    </el-form-item>
    <el-form-item label="适用场景" required :error="errors.when_text">
      <el-input v-model="modelValue.when_text" placeholder="什么情况下应该用到它" />
      <div class="field-meta">
        <span class="hint">助手根据标题与适用场景判断是否需要读取全文。</span>
        <span class="word-count" :class="{ 'is-over': whenLength > 120 }">{{ whenLength }} / 120</span>
      </div>
    </el-form-item>
    <el-form-item label="内容" required :error="errors.body">
      <el-input
        v-model="modelValue.body"
        type="textarea"
        :rows="bodyLimit > 500 ? 12 : 6"
        placeholder="具体做法、业务口径或参考文档，支持粘贴长文。不要写具体数值或客户名。"
      />
      <div class="field-meta">
        <span class="word-count body-word-count" :class="{ 'is-over': bodyLength > bodyLimit }">
          {{ bodyLength }} / {{ bodyLimit }}
        </span>
      </div>
    </el-form-item>
    <el-form-item label="作用于" required :error="errors.target">
      <el-select v-model="modelValue.target" style="width: 100%" filterable>
        <el-option label="全局（所有助手）" value="global" />
        <el-option v-for="agent in agents" :key="agent.agent_id" :value="agent.agent_id" :label="agent.name" />
      </el-select>
    </el-form-item>
  </el-form>
</template>
<script setup>
import { computed, reactive, ref, watch } from 'vue'

const props = defineProps({
  modelValue: { type: Object, required: true },
  agents: { type: Array, default: () => [] },
  source: { type: String, default: 'manual' }
})

// Count Unicode code points to match backend Python len() (avoiding UTF-16 surrogate double-counting).
const codePointLength = value => Array.from(String(value || '')).length

const bodyLimit = computed(() => (props.source === 'digest' ? 500 : 20000))
const titleLength = computed(() => codePointLength(props.modelValue.title))
const whenLength = computed(() => codePointLength(props.modelValue.when_text))
const bodyLength = computed(() => codePointLength(props.modelValue.body))
const errors = reactive({})
const submitted = ref(false)

function validate() {
  submitted.value = true
  for (const [field, limit] of Object.entries({ title: 40, when_text: 120, body: bodyLimit.value, target: Infinity })) {
    const value = String(props.modelValue[field] || '').trim()
    errors[field] = !value ? '请填写此项' : codePointLength(value) > limit ? `最多 ${limit} 字` : ''
  }
  return Object.values(errors).every(error => !error)
}

watch(() => [props.modelValue, bodyLimit.value], () => { if (submitted.value) validate() }, { deep: true })
defineExpose({ validate, bodyLimit })
</script>
<style scoped>
.field-meta { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; width: 100%; margin-top: 5px; }
.hint { color: #64748b; font-size: 12px; line-height: 1.5; }
.word-count { margin-left: auto; color: #909399; font-size: 12px; line-height: 1.5; font-variant-numeric: tabular-nums; white-space: nowrap; }
.word-count.is-over { color: #f56c6c; }
</style>
