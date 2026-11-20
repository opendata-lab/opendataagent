<template>
  <el-form :model="modelValue" label-position="top" class="entry-fields">
    <el-form-item label="标题" required :error="errors.title">
      <el-input v-model="modelValue.title" maxlength="40" show-word-limit placeholder="一句话说明这条内容" />
    </el-form-item>
    <el-form-item label="适用条件" required :error="errors.when_text">
      <el-input v-model="modelValue.when_text" maxlength="120" show-word-limit placeholder="什么情况下应该用到它" />
      <div class="hint">按需读取时，助手根据适用条件判断是否需要读取全文。</div>
    </el-form-item>
    <el-form-item label="内容" required :error="errors.body">
      <el-input v-model="modelValue.body" type="textarea" :rows="5" maxlength="500" show-word-limit placeholder="具体怎么做、要避免什么。不要写具体数据或业务口径定义。" />
    </el-form-item>
    <el-form-item label="作用于" required :error="errors.target">
      <el-select v-model="modelValue.target" style="width: 100%" filterable>
        <el-option label="全局（所有助手）" value="global" />
        <el-option v-for="agent in agents" :key="agent.agent_id" :value="agent.agent_id" :label="agent.name" />
      </el-select>
    </el-form-item>
    <el-form-item label="注入方式" required :error="errors.inject_mode">
      <el-radio-group v-model="modelValue.inject_mode" class="inject-options">
        <el-radio value="always">始终注入</el-radio>
        <div class="hint">每次任务带上全文，适合必须遵守的规则</div>
        <el-radio value="on_demand">按需读取</el-radio>
        <div class="hint">只列标题与适用条件，相关时再读全文</div>
      </el-radio-group>
    </el-form-item>
  </el-form>
</template>
<script setup>
import { reactive, ref, watch } from 'vue'
const props = defineProps({ modelValue: { type: Object, required: true }, agents: { type: Array, default: () => [] } })
const errors = reactive({}), submitted = ref(false)
function validate() {
  submitted.value = true
  for (const [field, limit] of Object.entries({ title: 40, when_text: 120, body: 500, target: Infinity, inject_mode: Infinity })) {
    const value = String(props.modelValue[field] || '').trim()
    errors[field] = !value ? '请填写此项' : Array.from(value).length > limit ? `最多 ${limit} 字` : ''
  }
  return Object.values(errors).every(error => !error)
}
watch(() => props.modelValue, () => { if (submitted.value) validate() }, { deep: true })
defineExpose({ validate })
</script>
<style scoped>
.hint { color: #64748b; font-size: 12px; line-height: 1.5; margin-top: 5px; }
.inject-options { display: block; }
.inject-options .hint { margin: 0 0 8px 24px; }
</style>
