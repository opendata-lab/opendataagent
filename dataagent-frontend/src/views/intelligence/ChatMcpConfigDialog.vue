<template>
  <el-dialog v-model="open" title="MCP 配置" width="min(560px, calc(100vw - 32px))" append-to-body>
    <el-form v-loading="loading" label-position="top" class="chat-mcp-form" @submit.prevent>
      <div v-if="loadError" role="alert" class="chat-mcp-status">
        {{ loadError }}
        <el-button link type="primary" @click="loadConfig">重试</el-button>
      </div>
      <p v-else-if="!loading && !profile?.remote_mcp_path" class="chat-mcp-status">当前助手未开启 MCP 访问。</p>
      <template v-else-if="!loading">
        <el-form-item label="访问令牌">
          <div class="chat-mcp-token">
            <el-select
              v-model="tokenId"
              aria-label="访问令牌"
              :placeholder="tokens.length ? '选择令牌' : '暂无有效令牌'"
              :disabled="!tokens.length"
              clearable
            >
              <el-option
                v-for="token in tokens"
                :key="token.id"
                :label="`${token.name}（${token.token_prefix}…）`"
                :value="token.id"
              />
            </el-select>
            <el-button
              :icon="Refresh"
              :loading="refreshingTokens"
              class="chat-mcp-refresh"
              aria-label="刷新令牌"
              title="刷新令牌"
              @click="refreshTokens"
            />
            <el-button link type="primary" class="chat-mcp-token-link" @click="createOpen = true">新增令牌</el-button>
          </div>
          <template v-if="selectedToken">
            <el-input
              v-model="tokenInput"
              type="password"
              autocomplete="off"
              aria-label="完整访问令牌"
              placeholder="粘贴创建时保存的完整令牌"
              class="chat-mcp-token-input"
            />
            <span v-if="tokenInput.trim() && !validToken" class="chat-mcp-token-error" role="alert">
              请粘贴与所选前缀匹配的完整令牌。
            </span>
          </template>
        </el-form-item>
        <el-form-item label="MCP 配置">
          <el-input
            :model-value="config"
            type="textarea"
            aria-label="MCP 配置 JSON"
            :rows="10"
            readonly
            resize="vertical"
            spellcheck="false"
            class="chat-mcp-json"
          />
        </el-form-item>
      </template>
    </el-form>
    <McpTokenCreateDialog v-model="createOpen" :config-agent-id="agentId" @created="onTokenCreated" />
    <template #footer>
      <el-button :disabled="!canExport" @click="downloadConfig">下载 JSON</el-button>
      <el-button type="primary" :disabled="!canExport" @click="copyConfig">复制配置</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { Refresh } from '@element-plus/icons-vue'
import { dataagentApi } from '@/api/dataagent'
import { copyText } from '@/utils/clipboard'
import { stringifyMcpClientConfig } from '@/utils/mcpClientConfig'
import { downloadTextFile } from '@/utils/tableExport'
import McpTokenCreateDialog from '@/views/settings/McpTokenCreateDialog.vue'

const props = defineProps({ agentId: { type: String, required: true } })
const open = defineModel({ type: Boolean, required: true })

const loading = ref(true)
const loadError = ref('')
const profile = ref(null)
const tokens = ref([])
const refreshingTokens = ref(false)
const tokenId = ref()
const tokenInput = ref('')
const createOpen = ref(false)

const selectedToken = computed(() => tokens.value.find(({ id }) => id === tokenId.value))
const validToken = computed(() => {
  const value = tokenInput.value.trim()
  return /^dak_[\w-]{43}$/.test(value) && value.startsWith(selectedToken.value?.token_prefix || 'dak_')
})
const config = computed(() => profile.value?.remote_mcp_path
  ? stringifyMcpClientConfig(
    profile.value.name,
    profile.value.remote_mcp_path,
    selectedToken.value && validToken.value ? tokenInput.value.trim() : undefined
  )
  : ''
)
const canExport = computed(() => Boolean(config.value && (!selectedToken.value || validToken.value)))

watch(tokenId, () => { tokenInput.value = '' }, { flush: 'sync' })

async function loadConfig() {
  loading.value = true
  loadError.value = ''
  try {
    const nextProfile = await dataagentApi.getAgentProfile(props.agentId)
    if (nextProfile?.remote_mcp_path) await loadTokens()
    profile.value = nextProfile
  } catch {
    loadError.value = '加载 MCP 配置失败。'
  } finally {
    loading.value = false
  }
}

async function loadTokens() {
  const { items } = await dataagentApi.listMcpTokens('mine')
  tokens.value = items.filter(({ status }) => status === 'active')
  if (!selectedToken.value) {
    tokenId.value = tokens.value.length === 1 ? tokens.value[0].id : undefined
  }
}

async function refreshTokens() {
  if (refreshingTokens.value) return
  refreshingTokens.value = true
  try {
    await loadTokens()
    ElMessage.success('令牌列表已刷新')
  } catch (error) {
    if (!error?.__odwNotified) ElMessage.error('刷新令牌失败，请重试')
  } finally {
    refreshingTokens.value = false
  }
}

// A token created here is selected and filled in, so the config is ready to copy.
async function onTokenCreated({ item, token }) {
  try {
    await loadTokens()
  } catch {
    return
  }
  if (tokens.value.some(({ id }) => id === item?.id)) {
    tokenId.value = item.id
    tokenInput.value = token
  }
}

async function copyConfig() {
  if (!canExport.value) return
  try {
    await copyText(config.value)
    ElMessage.success('已复制 MCP 配置')
  } catch {
    ElMessage.error('无法访问剪贴板，请检查浏览器权限后重试')
  }
}

function downloadConfig() {
  if (canExport.value) downloadTextFile('mcp.json', config.value, 'application/json')
}

onMounted(loadConfig)
</script>

<style scoped>
.chat-mcp-form {
  min-height: 56px;
}

.chat-mcp-status {
  margin: 0;
}

.chat-mcp-token {
  display: flex;
  align-items: center;
  gap: 12px;
  width: 100%;
}

.chat-mcp-token :deep(.el-select) {
  min-width: 0;
  flex: 1;
}

.chat-mcp-refresh {
  flex: none;
  width: 32px;
  padding: 0;
}

.chat-mcp-token-link {
  flex: none;
  color: #245db1;
}

.chat-mcp-token-input {
  margin-top: 8px;
}

.chat-mcp-token-error {
  color: #a22b2b;
}

.chat-mcp-json :deep(textarea) {
  font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
  font-size: 13px;
}
</style>
