<template>
  <div class="experience-view">
    <div class="experience-view__header">
      <div>
        <div class="experience-view__title-row">
          <div class="experience-view__title">经验库</div>
          <div class="experience-view__flags">
            <el-tooltip content="由部署配置 DATAAGENT_EXPERIENCE_DIGEST_ENABLED 控制" placement="top">
              <el-tag :type="digestEnabled ? 'success' : 'info'" size="small" data-testid="experience-digest-status">
                每日总结 {{ digestEnabled ? '已开启' : '未开启' }}
              </el-tag>
            </el-tooltip>
            <el-tooltip content="由部署配置 DATAAGENT_EXPERIENCE_INJECTION_ENABLED 控制" placement="top">
              <el-tag :type="injectionEnabled ? 'success' : 'info'" size="small" data-testid="experience-injection-status">
                注入 {{ injectionEnabled ? '已开启' : '未开启' }}
              </el-tag>
            </el-tooltip>
          </div>
        </div>
        <div class="experience-view__subtitle">每日总结产生候选，审核后注入助手的系统提示词</div>
      </div>
      <el-button type="primary" data-testid="experience-create-btn" @click="openCreateDialog">新增经验</el-button>
    </div>

    <div class="experience-view__controls">
      <el-tabs v-model="activeStatus" class="experience-view__tabs" data-testid="experience-tabs">
        <el-tab-pane label="候选" name="candidate" />
        <el-tab-pane label="生效" name="active" />
        <el-tab-pane label="已停用" name="retired" />
      </el-tabs>

      <div class="experience-view__filters">
        <el-select v-model="filters.agentId" clearable placeholder="全部助手" class="experience-view__select" data-testid="experience-agent">
          <el-option v-for="agent in agents" :key="agent.agent_id" :label="agent.name" :value="agent.agent_id" />
        </el-select>
        <el-select v-model="filters.scope" placeholder="全部" class="experience-view__select-sm" data-testid="experience-scope">
          <el-option label="全部" value="" />
          <el-option label="助手级" value="assistant" />
          <el-option label="平台级" value="platform" />
        </el-select>
      </div>
    </div>

    <el-table v-loading="loading" :data="items" stripe class="experience-view__table" empty-text="暂无经验">
      <el-table-column label="内容" min-width="320">
        <template #default="{ row }">
          <div class="experience-view__content">{{ row.content || '—' }}</div>
        </template>
      </el-table-column>
      <el-table-column label="范围" width="95">
        <template #default="{ row }">
          <el-tag :type="row.scope === 'platform' ? 'primary' : 'info'" size="small">
            {{ row.scope === 'platform' ? '平台级' : '助手级' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="助手" width="140">
        <template #default="{ row }">
          {{ row.scope === 'platform' ? '全部助手' : agentName(row.agent_id) }}
        </template>
      </el-table-column>
      <el-table-column label="来源" width="200">
        <template #default="{ row }">
          <div class="experience-view__source">
            <el-link
              v-if="row.digest_topic_id"
              type="primary"
              :data-testid="`experience-digest-${row.experience_id}`"
              @click="openDigestTopic(row)"
            >
              查看总结会话
            </el-link>
            <!-- The tasks the entry was drawn from: main-site ones open in /chat;
                 Widget conversations cannot be opened by URL, so they are only named. -->
            <template v-for="(src, index) in row.sources || []" :key="src.task_id">
              <el-link
                v-if="src.source === 'web'"
                type="primary"
                :data-testid="`experience-source-${src.task_id}`"
                @click="openSource(src)"
              >来源 {{ index + 1 }}</el-link>
              <el-tag v-else size="small" type="info" title="Widget 会话无法从这里打开">Widget 来源 {{ index + 1 }}</el-tag>
            </template>
            <span v-if="!row.digest_topic_id && !sourceTaskCount(row)" class="experience-view__source-muted">人工新增</span>
          </div>
        </template>
      </el-table-column>
      <el-table-column label="更新时间" width="170">
        <template #default="{ row }">{{ formatTime(row.updated_at) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="260">
        <template #default="{ row }">
          <div class="experience-view__actions">
            <template v-if="row.status === 'candidate'">
              <el-button
                link
                type="primary"
                size="small"
                :disabled="actingId === row.experience_id"
                :data-testid="`experience-activate-assistant-${row.experience_id}`"
                @click="applyUpdate(row, { status: 'active', scope: 'assistant' }, '已设为助手级生效')"
              >
                设为助手级
              </el-button>
              <el-button
                link
                type="primary"
                size="small"
                :disabled="actingId === row.experience_id"
                :data-testid="`experience-activate-platform-${row.experience_id}`"
                @click="applyUpdate(row, { status: 'active', scope: 'platform' }, '已设为平台级生效')"
              >
                设为平台级
              </el-button>
              <el-button
                link
                type="primary"
                size="small"
                :disabled="actingId === row.experience_id"
                :data-testid="`experience-edit-${row.experience_id}`"
                @click="openEditDialog(row)"
              >
                编辑
              </el-button>
              <el-button
                link
                type="danger"
                size="small"
                :disabled="actingId === row.experience_id"
                :data-testid="`experience-ignore-${row.experience_id}`"
                @click="applyUpdate(row, { status: 'retired' }, '已忽略')"
              >
                忽略
              </el-button>
            </template>

            <template v-else-if="row.status === 'active'">
              <el-button
                link
                type="primary"
                size="small"
                :disabled="actingId === row.experience_id"
                :data-testid="`experience-edit-${row.experience_id}`"
                @click="openEditDialog(row)"
              >
                编辑
              </el-button>
              <el-button
                v-if="row.scope === 'assistant'"
                link
                type="primary"
                size="small"
                :disabled="actingId === row.experience_id"
                :data-testid="`experience-promote-${row.experience_id}`"
                @click="applyUpdate(row, { scope: 'platform' }, '已提升为平台级')"
              >
                提升为平台级
              </el-button>
              <el-button
                link
                type="danger"
                size="small"
                :disabled="actingId === row.experience_id"
                :data-testid="`experience-retire-${row.experience_id}`"
                @click="applyUpdate(row, { status: 'retired' }, '已停用')"
              >
                停用
              </el-button>
            </template>

            <template v-else-if="row.status === 'retired'">
              <el-button
                link
                type="primary"
                size="small"
                :disabled="actingId === row.experience_id"
                :data-testid="`experience-restore-${row.experience_id}`"
                @click="applyUpdate(row, { status: 'candidate' }, '已恢复为候选')"
              >
                恢复为候选
              </el-button>
            </template>
          </div>
        </template>
      </el-table-column>
    </el-table>

    <el-pagination
      v-if="total > pageSize"
      v-model:current-page="page"
      :page-size="pageSize"
      :total="total"
      layout="prev, pager, next, total"
      class="experience-view__pager"
    />

    <el-dialog v-model="editDialog.visible" title="编辑经验" width="520px" destroy-on-close>
      <el-form label-position="top">
        <el-form-item label="经验内容" required :error="editDialog.error">
          <el-input
            v-model="editDialog.content"
            type="textarea"
            :rows="5"
            :maxlength="500"
            show-word-limit
            placeholder="请输入经验内容（最多 500 字）"
            data-testid="experience-edit-content"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editDialog.visible = false">取消</el-button>
        <el-button type="primary" :loading="editDialog.saving" data-testid="experience-edit-submit" @click="submitEdit">
          保存
        </el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="createDialog.visible" title="新增经验" width="520px" destroy-on-close>
      <el-form label-position="top">
        <el-form-item label="范围" required>
          <el-radio-group v-model="createDialog.scope" data-testid="experience-create-scope">
            <el-radio value="platform">平台级（全部助手）</el-radio>
            <el-radio value="assistant">助手级</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item
          v-if="createDialog.scope === 'assistant'"
          label="助手"
          required
          :error="createDialog.errors.agentId"
        >
          <el-select
            v-model="createDialog.agentId"
            placeholder="请选择助手"
            class="experience-view__full-width"
            data-testid="experience-create-agent"
          >
            <el-option v-for="agent in agents" :key="agent.agent_id" :label="agent.name" :value="agent.agent_id" />
          </el-select>
        </el-form-item>
        <el-form-item label="经验内容" required :error="createDialog.errors.content">
          <el-input
            v-model="createDialog.content"
            type="textarea"
            :rows="5"
            :maxlength="500"
            show-word-limit
            placeholder="请输入经验内容（最多 500 字）"
            data-testid="experience-create-content"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createDialog.visible = false">取消</el-button>
        <el-button type="primary" :loading="createDialog.saving" data-testid="experience-create-submit" @click="submitCreate">
          确定
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { dataagentApi } from '@/api/dataagent'

const router = useRouter()
const pageSize = 20

const agents = ref([])
const items = ref([])
const total = ref(0)
const page = ref(1)
const loading = ref(false)
const actingId = ref('')
const activeStatus = ref('candidate')
const digestEnabled = ref(false)
const injectionEnabled = ref(false)
const digestAgentId = ref('')
const filters = reactive({ agentId: '', scope: '' })

const editDialog = reactive({
  visible: false,
  experienceId: '',
  content: '',
  saving: false,
  error: ''
})

const createDialog = reactive({
  visible: false,
  scope: 'platform',
  agentId: '',
  content: '',
  saving: false,
  errors: { agentId: '', content: '' }
})

let loadSeq = 0

const agentName = (agentId) => agents.value.find((agent) => agent.agent_id === agentId)?.name || agentId || '—'
const formatTime = (value) => (value ? String(value).replace('T', ' ').slice(0, 19) : '—')
const sourceTaskCount = (row) => (Array.isArray(row?.source_task_ids) ? row.source_task_ids.length : 0)

async function loadAgents() {
  try {
    const data = await dataagentApi.listAgentProfiles()
    const list = Array.isArray(data) ? data : (data?.items || data?.list || [])
    agents.value = list.filter((agent) => agent?.agent_id)
  } catch {
    agents.value = []
  }
}

async function load() {
  const seq = ++loadSeq
  loading.value = true
  try {
    const params = {
      status: activeStatus.value,
      page: page.value,
      page_size: pageSize
    }
    if (filters.agentId) params.agent_id = filters.agentId
    if (filters.scope) params.scope = filters.scope
    const data = await dataagentApi.listExperiences(params)
    if (seq !== loadSeq) return
    items.value = Array.isArray(data?.items) ? data.items : []
    total.value = Number(data?.total || 0)
    digestEnabled.value = Boolean(data?.digest_enabled)
    injectionEnabled.value = Boolean(data?.injection_enabled)
    digestAgentId.value = String(data?.digest_agent_id || '')
  } catch (error) {
    if (seq !== loadSeq) return
    items.value = []
    if (!error?.__odwNotified) ElMessage.error(error?.message || '加载经验库失败')
  } finally {
    if (seq === loadSeq) loading.value = false
  }
}

// Pass digest_agent_id alongside topic_id so the chat page keeps the digest
// conversation selected even when another assistant was previously active.
function openDigestTopic(row) {
  if (!row?.digest_topic_id) return
  router.push({
    path: '/chat',
    query: { agent_id: digestAgentId.value, topic_id: row.digest_topic_id }
  })
}

function openSource(src) {
  router.push({ path: '/chat', query: { agent_id: src.agent_id, topic_id: src.topic_id } })
}

async function applyUpdate(row, payload, successText) {
  actingId.value = row.experience_id
  try {
    await dataagentApi.updateExperience(row.experience_id, payload)
    ElMessage.success(successText)
    await load()
  } catch (error) {
    if (!error?.__odwNotified) ElMessage.error(error?.message || '操作失败')
  } finally {
    actingId.value = ''
  }
}

function openEditDialog(row) {
  editDialog.experienceId = row.experience_id
  editDialog.content = String(row.content || '')
  editDialog.error = ''
  editDialog.visible = true
}

async function submitEdit() {
  const content = editDialog.content.trim()
  if (!content) {
    editDialog.error = '请输入经验内容'
    return
  }
  editDialog.error = ''
  editDialog.saving = true
  try {
    await dataagentApi.updateExperience(editDialog.experienceId, { content })
    editDialog.visible = false
    ElMessage.success('已更新经验')
    await load()
  } catch (error) {
    if (!error?.__odwNotified) ElMessage.error(error?.message || '更新失败')
  } finally {
    editDialog.saving = false
  }
}

function openCreateDialog() {
  createDialog.scope = 'platform'
  createDialog.agentId = ''
  createDialog.content = ''
  createDialog.errors.agentId = ''
  createDialog.errors.content = ''
  createDialog.visible = true
}

async function submitCreate() {
  createDialog.errors.agentId = ''
  createDialog.errors.content = ''

  if (createDialog.scope === 'assistant' && !createDialog.agentId) {
    createDialog.errors.agentId = '请选择助手'
  }
  const content = createDialog.content.trim()
  if (!content) {
    createDialog.errors.content = '请输入经验内容'
  }
  if (createDialog.errors.agentId || createDialog.errors.content) return

  const payload = { scope: createDialog.scope, content }
  if (createDialog.scope === 'assistant') {
    payload.agent_id = createDialog.agentId
  }

  createDialog.saving = true
  try {
    await dataagentApi.createExperience(payload)
    createDialog.visible = false
    ElMessage.success('已新增经验')
    // Newly created experiences start as active, so show the active tab.
    if (activeStatus.value !== 'active') {
      activeStatus.value = 'active'
    } else if (page.value !== 1) {
      page.value = 1
    } else {
      await load()
    }
  } catch (error) {
    if (!error?.__odwNotified) ElMessage.error(error?.message || '新增失败')
  } finally {
    createDialog.saving = false
  }
}

watch(() => createDialog.scope, (scope) => {
  if (scope !== 'assistant') createDialog.errors.agentId = ''
})

watch(() => [activeStatus.value, filters.agentId, filters.scope], () => {
  if (page.value !== 1) page.value = 1
  else load()
})
watch(page, load)

onMounted(() => {
  loadAgents()
  load()
})
</script>

<style scoped>
.experience-view { display: flex; flex-direction: column; gap: 12px; }
.experience-view__header { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; flex-wrap: wrap; }
.experience-view__title-row { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.experience-view__title { font-size: 18px; font-weight: 600; }
.experience-view__flags { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.experience-view__subtitle { color: #6b7280; font-size: 13px; margin-top: 4px; }
.experience-view__controls { display: flex; justify-content: space-between; align-items: center; gap: 12px; flex-wrap: wrap; }
.experience-view__tabs { flex: 1 1 auto; min-width: 220px; }
.experience-view__tabs :deep(.el-tabs__header) { margin-bottom: 0; }
.experience-view__filters { display: flex; gap: 8px; flex-wrap: wrap; }
.experience-view__select { width: 180px; }
.experience-view__select-sm { width: 120px; }
.experience-view__full-width { width: 100%; }
.experience-view__content { white-space: pre-wrap; word-break: break-word; line-height: 1.5; }
.experience-view__source { display: flex; flex-direction: column; align-items: flex-start; gap: 2px; }
.experience-view__source-count { color: #6b7280; font-size: 12px; }
.experience-view__source-muted { color: #9ca3af; font-size: 12px; }
.experience-view__actions { display: flex; align-items: center; flex-wrap: wrap; gap: 4px 8px; }
.experience-view__actions :deep(.el-button + .el-button) { margin-left: 0; }
.experience-view__pager { justify-content: flex-end; }
</style>
