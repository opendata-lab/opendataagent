<template>
  <section class="library-view">
    <header class="library-head">
      <div><h1>{{ isMemory ? '记忆' : '知识' }}</h1><p class="muted">{{ isMemory ? '从真实会话中提炼，审核后供助手使用。' : '把助手需要遵守的做法写成知识，保存后下一次任务即生效。' }}</p></div>
      <el-button v-if="!isMemory" type="primary" @click="openDrawer(null, true)">新增知识</el-button>
    </header>
    <p v-if="flagsLoaded" class="deployment-note">注入{{ injectionEnabled ? '已开启' : '未开启' }}<template v-if="isMemory"> · 每日总结{{ digestEnabled ? '已开启' : '未开启' }}</template></p>
    <el-tabs v-if="isMemory" v-model="status">
      <el-tab-pane :label="`待审核${pendingMemoryCount ? ` ${pendingMemoryCount}` : ''}`" name="pending" />
      <el-tab-pane label="生效" name="active" /><el-tab-pane label="已停用" name="disabled" />
    </el-tabs>
    <div class="filters">
      <el-select v-if="!isMemory" v-model="status" aria-label="状态"><el-option label="生效" value="active" /><el-option label="已停用" value="disabled" /></el-select>
      <el-select v-model="target" aria-label="范围" placeholder="全部范围" clearable filterable>
        <el-option label="全局" value="global" /><el-option v-for="agent in agents" :key="agent.agent_id" :label="agent.name" :value="agent.agent_id" />
      </el-select>
      <el-input v-model="keyword" clearable placeholder="搜索标题或适用条件" aria-label="搜索标题或适用条件" />
      <span class="muted count">共 {{ total }} 条</span>
    </div>
    <el-alert v-if="loadError" type="error" :closable="false" show-icon><template #title>{{ loadError }} <el-button link @click="load">重新加载</el-button></template></el-alert>
    <div v-loading="loading" class="library-content">
      <div v-if="isMemory && status === 'pending' && items.length" class="review-layout">
        <aside class="review-queue" aria-label="待审核队列">
          <div class="queue-head">待审核 {{ total }} 条</div>
          <button v-for="item in items" :key="item.experience_id" :aria-current="selectedId === item.experience_id ? 'true' : undefined" @click="selectCandidate(item)">
            <strong>{{ item.title }}</strong><span>{{ scopeName(item) }} · {{ formatDate(item.created_at) }}</span>
          </button>
        </aside>
        <div v-if="selected" class="review-editor" :key="selectedId">
          <ExperienceFields ref="reviewFields" v-model="reviewForm" :agents="agents" />
          <ExperienceSources :item="selected" :digest-agent-id="digestAgentId" />
          <div class="review-actions">
            <div><el-button :disabled="acting" @click="changeStatus(selected, 'disabled')">忽略</el-button><el-button link type="danger" :disabled="acting" @click="remove(selected)">删除</el-button></div>
            <el-button type="primary" :loading="acting" @click="approve">通过并生效</el-button>
          </div>
        </div>
      </div>
      <el-table v-else-if="!(isMemory && status === 'pending')" :data="items" @row-click="row => openDrawer(row, false)" class="entry-table" :empty-text="status === 'active' ? `还没有生效的${isMemory ? '记忆' : '知识'}` : '没有已停用的条目'">
        <el-table-column label="标题 / 适用条件" min-width="280"><template #default="{ row }"><div class="entry-title">{{ row.title }}</div><div class="entry-condition">{{ row.when_text }}</div></template></el-table-column>
        <el-table-column label="作用于" min-width="140"><template #default="{ row }"><span class="muted">{{ scopeName(row) }}</span></template></el-table-column>
        <el-table-column label="注入" width="80"><template #default="{ row }"><span class="muted">{{ row.inject_mode === 'always' ? '始终' : '按需' }}</span></template></el-table-column>
        <el-table-column label="修改时间" width="110"><template #default="{ row }"><span class="muted">{{ formatDate(row.updated_at) }}</span></template></el-table-column>
        <el-table-column label="操作" width="120"><template #default="{ row }"><div class="row-actions" @click.stop>
          <el-button link type="primary" @click="openDrawer(row, true)">编辑</el-button>
          <el-dropdown trigger="click" @command="command => handleCommand(command, row)">
            <el-button text :icon="MoreFilled" aria-label="更多操作" :disabled="acting" />
            <template #dropdown><el-dropdown-menu><el-dropdown-item command="toggle">{{ row.status === 'active' ? '停用' : '恢复' }}</el-dropdown-item><el-dropdown-item command="inject">改为{{ row.inject_mode === 'always' ? '按需读取' : '始终注入' }}</el-dropdown-item><el-dropdown-item v-if="row.scope !== 'global'" command="promote">提升为全局</el-dropdown-item><el-dropdown-item command="delete" divided>删除</el-dropdown-item></el-dropdown-menu></template>
          </el-dropdown>
        </div></template></el-table-column>
      </el-table>
      <el-empty v-else-if="!loading && !loadError" :description="target || keyword ? '没有符合筛选条件的待审核记忆' : '全部审核完了，每日总结会带来新的候选。'" />
    </div>
    <el-pagination v-if="total > pageSize" v-model:current-page="page" :page-size="pageSize" :total="total" layout="prev, pager, next, total" />
    <ExperienceDrawer v-model="drawerOpen" :item="drawerItem" :editing="drawerEditing" :agents="agents" :digest-agent-id="digestAgentId" @saved="reloadAfterAction" @edit="drawerEditing = true" @delete="remove" @change="({ item, changes }) => changeStatus(item, changes.status, changes)" />
  </section>
</template>
<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { MoreFilled } from '@element-plus/icons-vue'
import { dataagentApi } from '@/api/dataagent'
import ExperienceFields from '@/components/experience/ExperienceFields.vue'
import ExperienceSources from '@/components/experience/ExperienceSources.vue'
import ExperienceDrawer from '@/components/experience/ExperienceDrawer.vue'
import { entryForm, entryPayload } from '@/components/experience/entryForm'
import { pendingMemoryCount, refreshMemoryCount } from '@/composables/experienceCounts'
const props = defineProps({ kind: { type: String, required: true } })
const route = useRoute()
const isMemory = computed(() => props.kind === 'memory')
const status = ref(isMemory.value ? 'pending' : 'active'), target = ref(String(route.query.agent_id || '')), keyword = ref(''), search = ref(''), page = ref(1), pageSize = 20
const items = ref([]), total = ref(0), agents = ref([]), selectedId = ref(''), reviewForm = ref(entryForm(null)), reviewFields = ref()
const loading = ref(false), loadError = ref(''), acting = ref(false), injectionEnabled = ref(false), digestEnabled = ref(false), flagsLoaded = ref(false), digestAgentId = ref('')
const drawerOpen = ref(false), drawerItem = ref(null), drawerEditing = ref(false)
const selected = computed(() => items.value.find(item => item.experience_id === selectedId.value))
let sequence = 0, timer
const scopeName = item => item.scope === 'global' ? '全局' : agents.value.find(a => a.agent_id === item.agent_id)?.name || item.agent_id
const formatDate = value => String(value || '').slice(5, 10)
function selectCandidate(item) { selectedId.value = item.experience_id; reviewForm.value = entryForm(item) }
function openDrawer(item, editing) { drawerItem.value = item; drawerEditing.value = editing; drawerOpen.value = true }
async function load() {
  const current = ++sequence
  loading.value = true; loadError.value = ''
  try {
    const params = { kind: props.kind, status: status.value, page: page.value, page_size: pageSize, keyword: search.value }
    if (target.value === 'global') params.scope = 'global'
    else if (target.value) { params.scope = 'agent'; params.agent_id = target.value }
    const data = await dataagentApi.listExperiences(params)
    if (sequence !== current) return
    items.value = data.items; total.value = data.total
    injectionEnabled.value = data.injection_enabled; digestEnabled.value = data.digest_enabled; digestAgentId.value = data.digest_agent_id; flagsLoaded.value = true
    if (page.value > 1 && !items.value.length) { page.value--; return }
    const candidate = items.value.find(item => item.experience_id === selectedId.value) || items.value[0]
    if (candidate) selectCandidate(candidate)
    else selectedId.value = ''
  } catch (error) {
    if (sequence === current) { items.value = []; total.value = 0; loadError.value = error?.message || '加载失败，请重试' }
  } finally { if (sequence === current) loading.value = false }
}
async function reloadAfterAction() { await Promise.all([load(), refreshMemoryCount()]) }
function handleCommand(command, item) {
  if (command === 'delete') return remove(item)
  if (command === 'inject') return changeStatus(item, item.status, { inject_mode: item.inject_mode === 'always' ? 'on_demand' : 'always' })
  if (command === 'promote') return changeStatus(item, item.status, { scope: 'global', agent_id: null })
  return changeStatus(item, item.status === 'active' ? 'disabled' : 'active')
}
async function changeStatus(item, nextStatus, fields = {}) {
  if (acting.value) return
  acting.value = true
  try {
    await dataagentApi.updateExperience(item.experience_id, { ...fields, status: nextStatus })
    drawerOpen.value = false
    ElMessage.success(nextStatus === 'active' ? '已生效，下一次任务开始使用' : item.status === 'pending' ? '已忽略，可在已停用中恢复' : '已停用')
    await reloadAfterAction()
  } catch (error) { if (!error?.__odwNotified) ElMessage.error(error?.message || '操作失败，请重试') }
  finally { acting.value = false }
}
async function approve() {
  if (!selected.value || !await reviewFields.value.validate()) return
  await changeStatus(selected.value, 'active', entryPayload(reviewForm.value))
}
async function remove(item) {
  if (acting.value) return
  try { await ElMessageBox.confirm(`删除「${item.title}」后无法恢复。`, '删除条目', { confirmButtonText: '删除', cancelButtonText: '取消', type: 'warning' }) }
  catch { return }
  acting.value = true
  try { await dataagentApi.deleteExperience(item.experience_id); drawerOpen.value = false; await reloadAfterAction(); ElMessage.success('已删除') }
  catch (error) { if (!error?.__odwNotified) ElMessage.error(error?.message || '删除失败，请重试') }
  finally { acting.value = false }
}
watch(keyword, value => { clearTimeout(timer); timer = setTimeout(() => { search.value = value.trim() }, 250) })
watch(() => props.kind, () => { status.value = isMemory.value ? 'pending' : 'active'; drawerOpen.value = false; keyword.value = ''; search.value = '' })
watch([status, target, search], () => { if (page.value !== 1) page.value = 1; else load() })
watch(page, load)
onMounted(async () => {
  load(); refreshMemoryCount()
  try { const data = await dataagentApi.listAgentProfiles(); agents.value = data }
  catch (error) { ElMessage.error(error?.message || '助手列表加载失败，请刷新页面') }
})
onBeforeUnmount(() => { sequence++; clearTimeout(timer) })
</script>
<style scoped>
.library-view { max-width: 1200px; margin: 0 auto; color: #1f2d3d; }
.library-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 16px; padding: 16px 0; }
h1 { font-size: 22px; line-height: 1.4; margin: 0 0 8px; font-weight: 600; }
.library-head p { margin: 0; font-size: 13px; }
.muted, .deployment-note { color: #64748b; }
.deployment-note { margin: 0 0 12px; font-size: 12px; }
.filters { display: flex; align-items: center; flex-wrap: wrap; gap: 12px; margin: 16px 0; }
.filters .el-select { width: 180px; }
.filters .el-input { width: 260px; }
.count { margin-left: auto; font-size: 12px; }
.library-content { min-height: 240px; }
.entry-title, .entry-condition { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.entry-title { color: #1f2d3d; font-weight: 500; }
.entry-condition { color: #64748b; font-size: 12px; margin-top: 3px; }
.entry-table :deep(.el-table__row) { cursor: pointer; }
.entry-table :deep(.el-table__cell) { padding: 14px 0; }
.row-actions { display: flex; align-items: center; gap: 8px; }
.review-layout { display: grid; grid-template-columns: 260px minmax(0, 1fr); background: #fff; border: 1px solid #dbe3ef; border-radius: 8px; }
.review-queue { border-right: 1px solid #dbe3ef; }
.queue-head { padding: 16px; font-size: 13px; color: #64748b; border-bottom: 1px solid #eef2f8; }
.review-queue button { display: block; text-align: left; border: 0; border-bottom: 1px solid #eef2f8; background: #fff; padding: 16px; width: 100%; cursor: pointer; color: #1f2d3d; }
.review-queue button:hover { background: #f4f7fb; }
.review-queue button[aria-current] { background: #eff6ff; }
.review-queue strong { display: block; font-size: 13px; line-height: 1.6; font-weight: 500; overflow-wrap: anywhere; }
.review-queue span { display: block; color: #64748b; font-size: 12px; margin-top: 5px; }
.review-editor { padding: 24px; max-width: 760px; min-width: 0; }
.review-actions { display: flex; gap: 12px; flex-wrap: wrap; justify-content: space-between; border-top: 1px solid #eef2f8; padding-top: 16px; margin-top: 24px; }
.el-pagination { margin-top: 16px; justify-content: flex-end; }
button:focus-visible { outline: 2px solid #2563eb; outline-offset: -2px; }
@media (max-width: 850px) { .review-layout { grid-template-columns: 1fr; } .review-queue { border-right: 0; max-height: 240px; overflow: auto; border-bottom: 1px solid #dbe3ef; } .review-editor { padding: 16px; } .filters .el-input { width: 100%; } }
</style>
