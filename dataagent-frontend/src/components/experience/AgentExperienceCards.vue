<template>
  <section class="agent-entries" aria-label="知识与记忆">
    <el-alert v-if="error" type="error" :closable="false"><template #title>{{ error }} <el-button link @click="load">重新加载</el-button></template></el-alert>
    <div v-loading="loading">
      <!-- The always-on budget only matters when it is exceeded or injection is
           off, so a notice replaces a permanent bar. -->
      <div v-if="loaded" class="injection-notes">
        <p v-if="preview.skipped_ids.length" class="warn">{{ preview.skipped_ids.length }} 条始终注入内容超出 {{ preview.budget }} 字额度，未注入。可改为按需读取或精简内容。</p>
        <p v-if="!preview.injection_enabled" class="muted">部署配置未开启注入，以下为开启后的内容。</p>
        <el-button link type="primary" @click="previewOpen = true">预览注入内容</el-button>
      </div>
      <section v-for="kind in ['knowledge', 'memory']" :key="kind" class="entry-card">
        <header><h3>{{ kind === 'knowledge' ? '知识' : '记忆' }}</h3><el-button link type="primary" @click="manage(kind)">管理</el-button></header>
        <p v-if="kind === 'memory' && preview.pending_count" class="muted">{{ preview.pending_count }} 条待审核</p>
        <template v-for="mode in ['always', 'on_demand']" :key="mode">
          <h4>{{ mode === 'always' ? '始终注入' : '按需读取' }} · {{ entries(kind, mode).length }} 条</h4>
          <ul>
            <li v-for="item in entries(kind, mode)" :key="item.experience_id">
              <button type="button" :class="{ skipped: !isIncluded(item) }" @click="selected = item; drawerOpen = true">
                <span>{{ item.title }}</span><small>{{ item.scope === 'global' ? '全局' : '本助手' }}{{ !isIncluded(item) ? ' · 未注入' : '' }}</small>
              </button>
            </li>
          </ul>
          <p v-if="!entries(kind, mode).length" class="muted empty">暂无{{ mode === 'always' ? '始终注入' : '按需读取' }}的条目</p>
        </template>
      </section>
    </div>
    <ExperienceDrawer v-model="drawerOpen" readonly :item="selected" :agents="[{ agent_id: agentId, name: agentName }]" :digest-agent-id="preview.digest_agent_id || ''" />
    <el-drawer v-model="previewOpen" title="预览注入内容" size="min(640px, 100vw)"><pre class="prompt-preview">{{ preview.prompt || '暂无可注入的内容' }}</pre></el-drawer>
  </section>
</template>
<script setup>
import { onBeforeUnmount, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { dataagentApi } from '@/api/dataagent'
import ExperienceDrawer from './ExperienceDrawer.vue'
const props = defineProps({ agentId: { type: String, required: true }, agentName: { type: String, default: '' } })
const router = useRouter(), preview = ref({ items: [], skipped_ids: [], included_ids: [], indexed_ids: [] })
const loading = ref(false), loaded = ref(false), error = ref(''), selected = ref(null), drawerOpen = ref(false), previewOpen = ref(false)
let sequence = 0
const entries = (kind, mode) => preview.value.items.filter(item => item.kind === kind && item.inject_mode === mode)
const isIncluded = item => (item.inject_mode === 'always' ? preview.value.included_ids : preview.value.indexed_ids).includes(item.experience_id)
function manage(kind) { router.push({ path: kind === 'knowledge' ? '/knowledge' : '/memories', query: { agent_id: props.agentId } }) }
async function load() {
  const current = ++sequence
  loading.value = true; error.value = ''; loaded.value = false
  try {
    const data = await dataagentApi.getExperienceInjectionPreview(props.agentId)
    if (sequence === current) { preview.value = data; loaded.value = true }
  } catch (err) { if (sequence === current) error.value = err?.message || '知识与记忆加载失败' }
  finally { if (sequence === current) loading.value = false }
}
watch(() => props.agentId, () => { drawerOpen.value = false; previewOpen.value = false; load() }, { immediate: true })
onBeforeUnmount(() => { sequence++ })
</script>
<style scoped>
.agent-entries { padding: 0 16px 20px; }
.entry-card { border-top: 1px solid #dbe3ef; padding: 18px 0; }
header { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
h3 { font-size: 14px; margin: 0; } h4 { color: #64748b; font-size: 12px; font-weight: 500; margin: 16px 0 6px; }
ul { margin: 0; padding: 0; list-style: none; }
li button { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; border: 0; border-radius: 4px; width: 100%; background: transparent; padding: 8px 0; text-align: left; color: #1f2d3d; cursor: pointer; }
li button:hover { background: #f4f7fb; } li span { min-width: 0; overflow-wrap: anywhere; } small { color: #64748b; white-space: nowrap; }
li button:focus-visible { outline: 2px solid #2563eb; } .skipped span { color: #64748b; }
.muted { color: #64748b; font-size: 12px; line-height: 1.6; margin: 8px 0; } .empty { margin: 0; }
.injection-notes { display: flex; flex-wrap: wrap; align-items: center; justify-content: flex-end; gap: 4px 12px; padding: 8px 0; }
.injection-notes p { flex: 1 1 100%; margin: 0; font-size: 12px; line-height: 1.6; } .warn { color: #b45309; }
.prompt-preview { white-space: pre-wrap; overflow-wrap: anywhere; font-family: inherit; font-size: 13px; line-height: 1.7; }
</style>
