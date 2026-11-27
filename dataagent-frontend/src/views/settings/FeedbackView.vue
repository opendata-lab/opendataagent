<template>
  <div class="feedback-view">
    <div class="feedback-view__toolbar">
      <div>
        <div class="feedback-view__title">用户反馈</div>
        <div class="feedback-view__subtitle">用户对回答的点赞与点踩，按评价时间倒序</div>
      </div>
      <div class="feedback-view__filters">
        <el-select v-model="filters.agentId" clearable placeholder="全部助手" class="feedback-view__select" data-testid="feedback-agent">
          <el-option v-for="agent in agents" :key="agent.agent_id" :label="agent.name" :value="agent.agent_id" />
        </el-select>
        <el-select v-model="filters.feedback" placeholder="全部评价" class="feedback-view__select-sm" data-testid="feedback-rating">
          <el-option label="全部评价" value="" />
          <el-option label="点赞" value="like" />
          <el-option label="点踩" value="dislike" />
        </el-select>
        <el-date-picker
          v-model="filters.range"
          type="daterange"
          value-format="YYYY-MM-DD"
          start-placeholder="开始日期"
          end-placeholder="结束日期"
          class="feedback-view__dates"
        />
      </div>
    </div>

    <div class="feedback-view__counts" data-testid="feedback-counts">
      <span>点赞 <strong>{{ counts.likes }}</strong></span>
      <span>点踩 <strong>{{ counts.dislikes }}</strong></span>
    </div>

    <el-table v-loading="loading" :data="items" stripe class="feedback-view__table" empty-text="暂无评价">
      <el-table-column label="评价时间" width="170">
        <template #default="{ row }">{{ formatTime(row.feedback_at) }}</template>
      </el-table-column>
      <el-table-column label="用户" width="150" show-overflow-tooltip>
        <template #default="{ row }">{{ row.user_display_name || row.username || (row.user_id != null ? `#${row.user_id}` : '未知用户') }}</template>
      </el-table-column>
      <el-table-column label="助手" width="140" show-overflow-tooltip>
        <template #default="{ row }">{{ agentName(row.agent_id) }}</template>
      </el-table-column>
      <el-table-column label="评价" width="80">
        <template #default="{ row }">
          <el-tag :type="row.feedback === 'dislike' ? 'danger' : 'success'" size="small">
            {{ row.feedback === 'dislike' ? '点踩' : '点赞' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="问题" min-width="200" show-overflow-tooltip>
        <template #default="{ row }">{{ row.question || '—' }}</template>
      </el-table-column>
      <el-table-column label="回答" min-width="260" show-overflow-tooltip>
        <template #default="{ row }">{{ row.answer || '—' }}</template>
      </el-table-column>
      <el-table-column label="会话" width="110">
        <template #default="{ row }">
          <el-link type="primary" :data-testid="`feedback-open-${row.message_id}`" @click="openConversation(row)">打开会话</el-link>
        </template>
      </el-table-column>
    </el-table>

    <el-pagination
      v-if="total > pageSize"
      v-model:current-page="page"
      :page-size="pageSize"
      :total="total"
      layout="prev, pager, next, total"
      class="feedback-view__pager"
    />
  </div>
</template>

<script setup>
import { onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { dataagentApi } from '@/api/dataagent'
import { conversationLocation } from '@/router/agentContext'
import { formatDateTime, localDayRange } from '@/utils/time'

const router = useRouter()
const pageSize = 20

const agents = ref([])
const items = ref([])
const total = ref(0)
const page = ref(1)
const loading = ref(false)
const counts = reactive({ likes: 0, dislikes: 0 })
const filters = reactive({ agentId: '', feedback: '', range: null })

const agentName = (agentId) => agents.value.find((agent) => agent.agent_id === agentId)?.name || agentId || '—'
const formatTime = (value) => formatDateTime(value)

async function loadAgents() {
  try {
    const data = await dataagentApi.listAgentProfiles()
    const list = Array.isArray(data) ? data : (data?.items || data?.list || [])
    agents.value = list.filter((agent) => agent?.agent_id)
  } catch {
    agents.value = []
  }
}

// Filters can change while a request is in flight; only the newest response
// may fill the table, counts and loading state.
let loadSeq = 0

async function load() {
  const seq = ++loadSeq
  loading.value = true
  try {
    const params = { page: page.value, page_size: pageSize }
    if (filters.agentId) params.agent_id = filters.agentId
    if (filters.feedback) params.feedback = filters.feedback
    if (Array.isArray(filters.range) && filters.range.length === 2) {
      const range = localDayRange(filters.range[0], filters.range[1])
      if (range) {
        params.start = range.start
        params.end = range.end
      }
    }
    const data = await dataagentApi.listFeedback(params)
    if (seq !== loadSeq) return
    items.value = Array.isArray(data?.items) ? data.items : []
    total.value = Number(data?.total || 0)
    counts.likes = Number(data?.likes || 0)
    counts.dislikes = Number(data?.dislikes || 0)
  } catch (error) {
    if (seq !== loadSeq) return
    items.value = []
    // The request layer already toasts most failures.
    if (!error?.__odwNotified) ElMessage.error(error?.message || '加载用户反馈失败')
  } finally {
    if (seq === loadSeq) loading.value = false
  }
}

function openConversation(row) {
  router.push(conversationLocation(row))
}

watch(() => [filters.agentId, filters.feedback, filters.range], () => {
  if (page.value !== 1) page.value = 1
  else load()
}, { deep: true })
watch(page, load)

onMounted(() => {
  loadAgents()
  load()
})
</script>

<style scoped>
.feedback-view { display: flex; flex-direction: column; gap: 12px; }
.feedback-view__toolbar { display: flex; justify-content: space-between; align-items: flex-end; gap: 12px; flex-wrap: wrap; }
.feedback-view__title { font-size: 18px; font-weight: 600; }
.feedback-view__subtitle { color: #6b7280; font-size: 13px; margin-top: 4px; }
.feedback-view__filters { display: flex; gap: 8px; flex-wrap: wrap; }
.feedback-view__select { width: 180px; }
.feedback-view__select-sm { width: 120px; }
.feedback-view__dates { width: 260px; }
.feedback-view__counts { display: flex; gap: 16px; color: #374151; font-size: 13px; }
.feedback-view__pager { justify-content: flex-end; }
</style>
