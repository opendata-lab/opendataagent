<template>
  <div class="mcp-tokens-view">
    <div class="mcp-tokens__toolbar">
      <div>
        <div class="mcp-tokens__title">访问令牌</div>
        <div class="mcp-tokens__subtitle">
          用于本地 MCP 客户端通过 Bearer Token 访问已开启 MCP 的智能体。
        </div>
      </div>
      <div class="mcp-tokens__actions">
        <el-button :icon="Refresh" @click="loadTokens">刷新</el-button>
        <el-button
          type="primary"
          :icon="Plus"
          data-testid="open-create-token"
          @click="createOpen = true"
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

    <McpTokenCreateDialog v-model="createOpen" @created="loadTokens" />
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import dayjs from 'dayjs'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Refresh } from '@element-plus/icons-vue'
import { dataagentApi } from '@/api/dataagent'
import McpTokenCreateDialog from './McpTokenCreateDialog.vue'

const STATUS_META = {
  active: { label: '有效', type: 'success' },
  revoked: { label: '已撤销', type: 'info' }
}

const tokens = ref([])
const loading = ref(false)
const revokingId = ref(null)
const createOpen = ref(false)

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
    const data = await dataagentApi.listMcpTokens()
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
</style>
