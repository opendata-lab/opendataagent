<template>
  <section class="agent-entries" aria-label="知识">
    <el-alert v-if="error" type="error" :closable="false">
      <template #title>{{ error }} <el-button link @click="load">重新加载</el-button></template>
    </el-alert>
    <div v-loading="loading">
      <div v-if="loaded" class="injection-notes">
        <p v-if="!preview.eligible && preview.reason === 'digest_agent'" class="muted">内置总结智能体不注入知识。</p>
        <p v-else-if="!preview.eligible" class="muted">部署配置未开启注入，运行时不会写入系统提示词。</p>
        <p v-else-if="preview.omitted_count > 0" class="muted">
          目录显示最新 {{ preview.indexed_ids.length }} 篇，另有 {{ preview.omitted_count }} 篇可通过搜索读取。
        </p>
        <el-button link type="primary" @click="previewOpen = true">预览注入内容</el-button>
      </div>
      <section class="entry-card">
        <header>
          <h3>知识 · {{ preview.items.length }} 条</h3>
          <el-button link type="primary" @click="manage()">管理</el-button>
        </header>
        <p v-if="preview.pending_count" class="muted">
          <el-button link type="primary" @click="manage('pending')">{{ preview.pending_count }} 条待审核</el-button>
        </p>
        <ul v-if="preview.items.length">
          <li v-for="item in preview.items" :key="item.experience_id">
            <button type="button" @click="selected = item; drawerOpen = true">
              <span>{{ item.title }}</span>
              <small>{{ item.scope === 'global' ? '全局' : '本助手' }} · {{ item.source === 'digest' ? '每日总结' : '管理员添加' }}</small>
            </button>
          </li>
        </ul>
        <p v-else class="muted empty">暂无生效的知识</p>
      </section>
    </div>
    <ExperienceDrawer
      v-model="drawerOpen"
      readonly
      :item="selected"
      :agents="[{ agent_id: agentId, name: agentName }]"
      :digest-agent-id="preview.digest_agent_id || ''"
    />
    <el-drawer v-model="previewOpen" title="预览注入内容" size="min(640px, 100vw)">
      <pre class="prompt-preview">{{ preview.prompt || '暂无可注入的内容' }}</pre>
    </el-drawer>
  </section>
</template>
<script setup>
import { onBeforeUnmount, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { dataagentApi } from '@/api/dataagent'
import ExperienceDrawer from './ExperienceDrawer.vue'

const props = defineProps({
  agentId: { type: String, required: true },
  agentName: { type: String, default: '' }
})

const router = useRouter()
const preview = ref({
  items: [],
  indexed_ids: [],
  omitted_count: 0,
  pending_count: 0,
  eligible: false,
  reason: '',
  injection_enabled: false,
  prompt: '',
  digest_agent_id: ''
})
const loading = ref(false)
const loaded = ref(false)
const error = ref('')
const selected = ref(null)
const drawerOpen = ref(false)
const previewOpen = ref(false)
let sequence = 0

function manage(status = '') {
  const query = status ? { agent_id: props.agentId, status } : { agent_id: props.agentId }
  router.push({ path: '/knowledge', query })
}

async function load() {
  const current = ++sequence
  loading.value = true
  error.value = ''
  loaded.value = false
  try {
    const data = await dataagentApi.getExperienceInjectionPreview(props.agentId)
    if (sequence === current) {
      preview.value = data
      loaded.value = true
    }
  } catch (err) {
    if (sequence === current) error.value = err?.message || '知识加载失败'
  } finally {
    if (sequence === current) loading.value = false
  }
}

watch(() => props.agentId, () => {
  drawerOpen.value = false
  previewOpen.value = false
  load()
}, { immediate: true })

onBeforeUnmount(() => { sequence++ })
</script>
<style scoped>
.agent-entries { padding: 0 16px 20px; }
.entry-card { border-top: 1px solid #dbe3ef; padding: 18px 0; }
header { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
h3 { font-size: 14px; margin: 0; }
ul { margin: 8px 0 0; padding: 0; list-style: none; }
li button { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; border: 0; border-radius: 4px; width: 100%; background: transparent; padding: 8px 0; text-align: left; color: #1f2d3d; cursor: pointer; }
li button:hover { background: #f4f7fb; }
li span { min-width: 0; overflow-wrap: anywhere; }
small { color: #64748b; white-space: nowrap; }
li button:focus-visible { outline: 2px solid #2563eb; }
.muted { color: #64748b; font-size: 12px; line-height: 1.6; margin: 8px 0; }
.empty { margin: 12px 0 0; }
.injection-notes { display: flex; flex-wrap: wrap; align-items: center; justify-content: flex-end; gap: 4px 12px; padding: 8px 0; }
.injection-notes p { flex: 1 1 100%; margin: 0; font-size: 12px; line-height: 1.6; }
.prompt-preview { white-space: pre-wrap; overflow-wrap: anywhere; font-family: inherit; font-size: 13px; line-height: 1.7; }
</style>
