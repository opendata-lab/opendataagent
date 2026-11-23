<template>
  <div class="mcp-tokens-view">
    <div class="mcp-tokens__toolbar">
      <div>
        <div class="mcp-tokens__title">访问令牌</div>
        <div class="mcp-tokens__subtitle">
          用于本地 MCP 客户端通过 Bearer Token 访问已开启 MCP 的智能体，每个用户最多 10 个有效令牌。仅展示有效和最近 30 天内失效的令牌。
        </div>
      </div>
      <div class="mcp-tokens__actions">
        <el-radio-group
          v-if="isAdmin"
          v-model="scope"
          data-testid="scope-switch"
        >
          <el-radio-button value="mine">我的</el-radio-button>
          <el-radio-button value="all">全部用户</el-radio-button>
        </el-radio-group>
        <el-button :icon="Refresh" @click="loadTokens">刷新</el-button>
        <el-button
          type="primary"
          :icon="Plus"
          data-testid="open-create-token"
          @click="openCreateDialog"
        >
          新建令牌
        </el-button>
      </div>
    </div>

    <el-table
      v-loading="loading"
      :data="tokens"
      stripe
      class="mcp-tokens__table"
      empty-text="暂无访问令牌"
    >
      <el-table-column prop="name" label="名称" min-width="160" show-overflow-tooltip />
      <el-table-column
        v-if="isAdmin && scope === 'all'"
        label="用户"
        min-width="150"
        show-overflow-tooltip
      >
        <template #default="{ row }">
          {{ row.display_name || row.user_id || '—' }}
        </template>
      </el-table-column>
      <el-table-column label="令牌前缀" width="150">
        <template #default="{ row }">
          <code class="token-prefix">{{ formatPrefix(row.token_prefix) }}</code>
        </template>
      </el-table-column>
      <el-table-column label="创建时间" width="170">
        <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
      </el-table-column>
      <el-table-column label="最后使用" width="170">
        <template #default="{ row }">{{ row.last_used_at ? formatTime(row.last_used_at) : '尚未使用' }}</template>
      </el-table-column>
      <el-table-column label="过期时间" width="170">
        <template #default="{ row }">{{ row.expires_at ? formatTime(row.expires_at) : '永不过期' }}</template>
      </el-table-column>
      <el-table-column label="状态" width="100">
        <template #default="{ row }">
          <el-tag :type="statusTagType(row.status)" size="small">
            {{ statusLabel(row.status) }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="100">
        <template #default="{ row }">
          <el-button
            v-if="row.status === 'active'"
            text
            type="danger"
            size="small"
            :loading="revokingId === row.id"
            :data-testid="`revoke-token-${row.id}`"
            @click="confirmRevoke(row)"
          >
            撤销
          </el-button>
          <span v-else class="action-muted">—</span>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog
      v-model="createDialogVisible"
      title="新建访问令牌"
      width="min(480px, calc(100vw - 32px))"
      :close-on-click-modal="false"
    >
      <el-form label-position="top" @submit.prevent="submitCreate">
        <el-form-item label="令牌名称" required :error="nameError">
          <el-input
            v-model="createForm.name"
            maxlength="64"
            show-word-limit
            placeholder="例如：ZCode 开发机"
            data-testid="token-name-input"
            @input="nameError = ''"
          />
        </el-form-item>
        <el-form-item label="有效期">
          <el-select
            v-model="createForm.expiry"
            style="width: 100%"
            data-testid="token-expiry-select"
          >
            <el-option
              v-for="option in EXPIRY_OPTIONS"
              :key="String(option.value)"
              :value="option.value"
              :label="option.label"
            />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button :disabled="creating" @click="createDialogVisible = false">取消</el-button>
        <el-button
          type="primary"
          :loading="creating"
          data-testid="submit-create-token"
          @click="submitCreate"
        >
          创建
        </el-button>
      </template>
    </el-dialog>

    <el-dialog
      v-model="tokenDialogVisible"
      title="令牌已创建"
      width="min(520px, calc(100vw - 32px))"
      :close-on-click-modal="false"
      :close-on-press-escape="false"
      @closed="clearCreatedToken"
    >
      <el-alert
        type="warning"
        :closable="false"
        show-icon
        title="请立即复制并保存此令牌，关闭弹窗后将无法再次查看完整令牌。"
      />
      <div class="created-token-box">
        <el-input
          :model-value="createdToken"
          readonly
          class="created-token-input"
          data-testid="created-token-value"
        />
        <el-button
          type="primary"
          :icon="CopyDocument"
          data-testid="copy-created-token"
          @click="copyCreatedToken"
        >
          复制令牌
        </el-button>
      </div>
      <template #footer>
        <el-button v-if="mcpConfig" @click="downloadConfig">下载 MCP 配置</el-button>
        <el-button v-if="mcpConfig" @click="copyConfig">复制 MCP 配置</el-button>
        <el-button
          type="primary"
          data-testid="close-created-token"
          @click="closeTokenDialog"
        >
          我已保存，关闭
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import dayjs from 'dayjs'
import { ElMessage, ElMessageBox } from 'element-plus'
import { CopyDocument, Plus, Refresh } from '@element-plus/icons-vue'
import { dataagentApi } from '@/api/dataagent'
import { useAuthStore } from '@/stores/auth'
import { copyText } from '@/utils/clipboard'
import { stringifyMcpClientConfig } from '@/utils/mcpClientConfig'
import { downloadTextFile } from '@/utils/tableExport'

const props = defineProps({ openCreate: Boolean, configAgentId: String })

const EXPIRY_OPTIONS = [
  { value: 30, label: '30 天' },
  { value: 90, label: '90 天' },
  { value: 180, label: '180 天' },
  { value: 'never', label: '永不过期' }
]

const STATUS_META = {
  active: { label: '有效', type: 'success' },
  expired: { label: '已过期', type: 'warning' },
  revoked: { label: '已撤销', type: 'info' }
}

const authStore = useAuthStore()
const isAdmin = computed(() => authStore.isAdmin)

const scope = ref('mine')
const tokens = ref([])
const loading = ref(false)
const creating = ref(false)
const revokingId = ref(null)

const createDialogVisible = ref(false)
const createForm = reactive({ name: '', expiry: 'never' })
const nameError = ref('')

const tokenDialogVisible = ref(false)
const createdToken = ref('')
const configAgent = ref(null)
const mcpConfig = computed(() => (
  createdToken.value && configAgent.value?.remote_mcp_path
    ? stringifyMcpClientConfig(configAgent.value.name, configAgent.value.remote_mcp_path, createdToken.value)
    : ''
))

const formatTime = (value) => (value ? dayjs(value).format('YYYY-MM-DD HH:mm:ss') : '—')

const formatPrefix = (prefix) => {
  const raw = String(prefix || '').trim()
  if (!raw) return '—'
  const withDak = raw.startsWith('dak_') ? raw : `dak_${raw}`
  return withDak.endsWith('…') || withDak.endsWith('...') ? withDak : `${withDak}…`
}

const statusLabel = (status) => STATUS_META[status]?.label || String(status || '—')
const statusTagType = (status) => STATUS_META[status]?.type || 'info'

let loadSeq = 0
async function loadTokens() {
  const seq = ++loadSeq
  loading.value = true
  try {
    const requestScope = isAdmin.value ? scope.value : 'mine'
    const data = await dataagentApi.listMcpTokens(requestScope)
    if (seq !== loadSeq) return
    tokens.value = Array.isArray(data?.items) ? data.items : []
  } catch (error) {
    if (seq !== loadSeq) return
    tokens.value = []
    if (!error?.__odwNotified) {
      ElMessage.error(error?.response?.data?.detail || error?.message || '加载令牌列表失败')
    }
  } finally {
    if (seq === loadSeq) loading.value = false
  }
}

function openCreateDialog() {
  createForm.name = ''
  createForm.expiry = 'never'
  nameError.value = ''
  createDialogVisible.value = true
}

async function submitCreate() {
  if (creating.value) return
  const trimmed = String(createForm.name || '').trim()
  if (!trimmed) {
    nameError.value = '请输入令牌名称'
    return
  }
  if (trimmed.length > 64) {
    nameError.value = '令牌名称不能超过 64 个字符'
    return
  }
  creating.value = true
  try {
    const expiresInDays = createForm.expiry === 'never' ? null : createForm.expiry
    const result = await dataagentApi.createMcpToken({
      name: trimmed,
      expires_in_days: expiresInDays
    })
    createDialogVisible.value = false
    createdToken.value = String(result?.token || '')
    tokenDialogVisible.value = true
    if (result?.item) {
      tokens.value = [result.item, ...tokens.value]
    } else {
      await loadTokens()
    }
  } catch (error) {
    if (!error?.__odwNotified) {
      ElMessage.error(error?.response?.data?.detail || error?.message || '创建令牌失败')
    }
  } finally {
    creating.value = false
  }
}

function clearCreatedToken() {
  createdToken.value = ''
}

function closeTokenDialog() {
  tokenDialogVisible.value = false
  clearCreatedToken()
}

async function copyCreatedToken() {
  if (!createdToken.value) return
  try {
    await copyText(createdToken.value)
    ElMessage.success('已复制令牌')
  } catch {
    ElMessage.error('无法访问剪贴板，请手动复制')
  }
}

async function copyConfig() {
  if (!mcpConfig.value) return
  try {
    await copyText(mcpConfig.value)
    ElMessage.success('已复制 MCP 配置')
  } catch {
    ElMessage.error('无法访问剪贴板，请手动复制令牌')
  }
}

function downloadConfig() {
  if (mcpConfig.value) downloadTextFile('mcp.json', mcpConfig.value, 'application/json')
}

async function confirmRevoke(row) {
  try {
    await ElMessageBox.confirm(
      `确认撤销令牌「${row.name}」吗？撤销后使用该令牌的 MCP 客户端将立即失效。`,
      '撤销访问令牌',
      {
        confirmButtonText: '撤销',
        cancelButtonText: '取消',
        type: 'warning'
      }
    )
  } catch {
    return
  }
  revokingId.value = row.id
  try {
    const result = await dataagentApi.revokeMcpToken(row.id)
    const updated = result?.item || result
    if (updated?.id) {
      tokens.value = tokens.value.map((item) => (item.id === updated.id ? updated : item))
    } else {
      await loadTokens()
    }
    ElMessage.success('令牌已撤销')
  } catch (error) {
    if (!error?.__odwNotified) {
      ElMessage.error(error?.response?.data?.detail || error?.message || '撤销令牌失败')
    }
  } finally {
    revokingId.value = null
  }
}

watch(scope, () => {
  loadTokens()
})

watch(isAdmin, (admin) => {
  if (!admin && scope.value !== 'mine') {
    scope.value = 'mine'
  }
})

watch(() => props.openCreate, (open) => {
  if (open) openCreateDialog()
}, { immediate: true })

watch(() => props.configAgentId, async (agentId, _, onCleanup) => {
  configAgent.value = null
  if (!agentId) return
  let current = true
  onCleanup(() => { current = false })
  try {
    const profile = await dataagentApi.getAgentProfile(agentId)
    if (current) configAgent.value = profile
  } catch {
    // A token can still be created or copied if its assistant is unavailable.
  }
}, { immediate: true })

onMounted(loadTokens)
</script>

<style scoped>
.mcp-tokens-view {
  display: flex;
  flex-direction: column;
  gap: 16px;
  color: #1f2937;
}

.mcp-tokens__toolbar {
  display: flex;
  justify-content: space-between;
  align-items: flex-end;
  gap: 16px;
  flex-wrap: wrap;
}

.mcp-tokens__title {
  font-size: 18px;
  font-weight: 600;
  color: #0f172a;
}

.mcp-tokens__subtitle {
  margin-top: 4px;
  font-size: 13px;
  color: #64748b;
}

.mcp-tokens__actions {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.mcp-tokens__table {
  width: 100%;
  border-radius: 8px;
  overflow: hidden;
}

.token-prefix {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 12px;
  padding: 2px 6px;
  border-radius: 4px;
  background: #f1f5f9;
  color: #334155;
}

.action-muted {
  color: #94a3b8;
}

.created-token-box {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 16px;
}

.created-token-input :deep(input) {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 13px;
}
</style>
