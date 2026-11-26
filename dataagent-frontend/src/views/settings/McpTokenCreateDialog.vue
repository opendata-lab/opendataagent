<template>
  <el-dialog
    v-model="open"
    :title="createdToken ? '令牌已创建' : '新建访问令牌'"
    width="min(520px, calc(100vw - 32px))"
    append-to-body
    :close-on-click-modal="!createdToken"
    @closed="reset"
  >
    <template v-if="createdToken">
      <p class="mcp-token-notice">请复制并保存完整令牌。关闭后，完整令牌无法再次查看。</p>
      <el-form label-position="top" @submit.prevent>
        <el-form-item label="访问令牌">
          <div class="mcp-token-result">
            <el-input
              :model-value="createdToken"
              readonly
              class="mcp-token-value"
              data-testid="created-token-value"
            />
            <el-button :icon="CopyDocument" data-testid="copy-created-token" @click="copyCreatedToken">
              复制令牌
            </el-button>
          </div>
        </el-form-item>
      </el-form>
      <section v-if="mcpConfig" class="mcp-token-export" aria-label="MCP 配置">
        <p>{{ configAgent.name }}的 MCP 配置</p>
        <div class="mcp-token-actions">
          <el-button type="primary" @click="copyConfig">复制 MCP 配置</el-button>
          <el-button @click="downloadConfig">下载 MCP 配置</el-button>
        </div>
      </section>
    </template>

    <el-form v-else label-position="top" data-testid="create-token-form" @submit.prevent="submitCreate">
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
    </el-form>

    <template #footer>
      <el-button v-if="createdToken" type="primary" data-testid="close-create-token" @click="open = false">完成</el-button>
      <template v-else>
        <el-button @click="open = false">取消</el-button>
        <el-button type="primary" :loading="creating" data-testid="submit-create-token" @click="submitCreate">
          创建
        </el-button>
      </template>
    </template>
  </el-dialog>
</template>

<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { CopyDocument } from '@element-plus/icons-vue'
import { dataagentApi } from '@/api/dataagent'
import { copyText } from '@/utils/clipboard'
import { stringifyMcpClientConfig } from '@/utils/mcpClientConfig'
import { downloadTextFile } from '@/utils/tableExport'

// configAgentId: when set (opened from an assistant's MCP dialog), the new token
// can be exported straight into that assistant's MCP configuration.
const props = defineProps({ configAgentId: String })
const open = defineModel({ type: Boolean, required: true })
const emit = defineEmits(['created'])

const createForm = reactive({ name: '' })
const nameError = ref('')
const creating = ref(false)
const createdToken = ref('')
const configAgent = ref(null)
const mcpConfig = computed(() => (
  createdToken.value && configAgent.value?.remote_mcp_path
    ? stringifyMcpClientConfig(configAgent.value.name, configAgent.value.remote_mcp_path, createdToken.value)
    : ''
))

async function submitCreate() {
  if (creating.value) return
  const name = createForm.name.trim()
  if (!name) {
    nameError.value = '请输入令牌名称'
    return
  }
  if (name.length > 64) {
    nameError.value = '令牌名称不能超过 64 个字符'
    return
  }
  creating.value = true
  try {
    const { item, token } = await dataagentApi.createMcpToken({ name })
    createdToken.value = token
    emit('created', { item, token })
  } catch (error) {
    if (!error?.__odwNotified) {
      ElMessage.error(error?.response?.data?.detail || error?.message || '创建令牌失败')
    }
  } finally {
    creating.value = false
  }
}

async function copyCreatedToken() {
  try {
    await copyText(createdToken.value)
    ElMessage.success('已复制令牌')
  } catch {
    ElMessage.error('无法访问剪贴板，请手动复制')
  }
}

async function copyConfig() {
  try {
    await copyText(mcpConfig.value)
    ElMessage.success('已复制 MCP 配置')
  } catch {
    ElMessage.error('无法访问剪贴板，请下载 MCP 配置')
  }
}

function downloadConfig() {
  downloadTextFile('mcp.json', mcpConfig.value, 'application/json')
}

function reset() {
  createForm.name = ''
  nameError.value = ''
  createdToken.value = ''
}

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
</script>

<style scoped>
.mcp-token-notice {
  margin: 0 0 20px;
  color: #475569;
  line-height: 1.6;
}

.mcp-token-result,
.mcp-token-actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  width: 100%;
}

.mcp-token-actions :deep(.el-button) {
  margin-left: 0;
}

.mcp-token-value {
  flex: 1 1 180px;
  min-width: 0;
}

.mcp-token-value :deep(input) {
  font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
  font-size: 13px;
}

.mcp-token-export {
  margin-top: 24px;
  padding-top: 16px;
  border-top: 1px solid #e2e8f0;
}

.mcp-token-export p {
  margin: 0 0 12px;
  font-weight: 600;
}
</style>
