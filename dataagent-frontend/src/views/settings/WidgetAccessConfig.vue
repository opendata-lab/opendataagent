<template>
  <div v-loading="loading" class="widget-access">
    <header class="page-header">
      <div class="page-header-main">
        <h2 class="page-title">Widget 接入白名单</h2>
        <p class="page-desc">
          管理允许嵌入智能问数 Widget 的站点。所有接入均要求已登录身份：浏览器同源嵌入使用平台会话，第三方系统通过服务端受信任代理携带访问密钥接入。
        </p>
      </div>
      <div class="page-header-actions">
        <el-button :icon="Plus" @click="addSite">新增站点</el-button>
        <el-button
          type="primary"
          :icon="Check"
          :loading="saving"
          :disabled="!isDirty || saving"
          @click="save"
        >
          {{ isDirty ? '保存改动' : '已保存' }}
        </el-button>
      </div>
    </header>

    <el-alert
      v-if="revealedKey"
      type="warning"
      show-icon
      :closable="true"
      class="key-alert"
      @close="revealedKey = null"
    >
      <template #title>
        站点「{{ revealedKey.website_id }}」的访问密钥已生成（仅展示一次，请立即复制到接入方服务端配置）
      </template>
      <div class="key-reveal-row">
        <code class="key-code" data-testid="revealed-access-key">{{ revealedKey.access_key }}</code>
        <el-button size="small" @click="copyKey(revealedKey.access_key)">复制</el-button>
      </div>
    </el-alert>

    <div v-if="!sites.length" class="empty-block">
      <div class="empty-title">暂无接入站点</div>
      <div class="empty-desc">点击右上角「新增站点」添加第一个允许嵌入 Widget 的站点。</div>
    </div>

    <div v-else class="site-list">
      <section v-for="(site, index) in sites" :key="site._key" class="site-card">
        <div class="site-card-head">
          <div class="site-index">站点 {{ index + 1 }}</div>
          <button type="button" class="site-remove" @click="removeSite(index)">
            <el-icon><Delete /></el-icon>
            <span>删除站点</span>
          </button>
        </div>

        <el-form label-position="top" class="site-form">
          <el-row :gutter="16">
            <el-col :xs="24" :md="12">
              <el-form-item>
                <template #label>
                  <span class="field-label">
                    Website ID
                    <el-tooltip content="站点唯一标识，需与嵌入脚本的 data-website-id 完全一致。" placement="top">
                      <el-icon><QuestionFilled /></el-icon>
                    </el-tooltip>
                  </span>
                </template>
                <el-input v-model="site.website_id" placeholder="例如 web-prod" />
              </el-form-item>
            </el-col>
            <el-col :xs="24" :md="12">
              <el-form-item>
                <template #label><span class="field-label">项目名称</span></template>
                <el-input v-model="site.project_name" placeholder="展示在 Widget 标题，可留空" />
              </el-form-item>
            </el-col>
          </el-row>

          <el-form-item>
            <template #label><span class="field-label">主题色</span></template>
            <div class="color-row">
              <el-color-picker v-model="site.project_color" />
              <el-input v-model="site.project_color" class="color-input" placeholder="#4A90A4" />
            </div>
          </el-form-item>

          <el-form-item>
            <template #label>
              <span class="field-label">
                允许来源（Origin）
                <el-tooltip placement="top">
                  <template #content>
                    浏览器嵌入页面的来源，例如 https://app.example.com。<br>
                    填写 <code>*</code> 表示放行所有来源（仅建议测试环境使用）。
                  </template>
                  <el-icon><QuestionFilled /></el-icon>
                </el-tooltip>
              </span>
            </template>
            <div class="origin-list">
              <div v-for="(_, oi) in site.allowed_origins" :key="oi" class="origin-row">
                <el-input
                  v-model="site.allowed_origins[oi]"
                  placeholder="https://app.example.com 或 *"
                  @keyup.enter="addOrigin(site)"
                />
                <el-button text :icon="Close" class="origin-remove" @click="removeOrigin(site, oi)" />
              </div>
              <el-button class="origin-add" :icon="Plus" @click="addOrigin(site)">添加来源</el-button>
              <div v-if="!site.allowed_origins.length" class="origin-hint">
                未配置来源时，仅放行同源或非浏览器（无 Origin 头）的请求。
              </div>
            </div>
          </el-form-item>

          <div class="server-side-box">
            <div class="server-side-head">
              <div>
                <div class="server-side-title">服务端受信任代理（OpenDataWorks / OntoFoundry 等）</div>
                <div class="server-side-desc">
                  启用后，该站点只能通过后端携带 <code>X-DataAgent-Access-Key</code> 与用户标识头接入。
                </div>
              </div>
              <div class="server-side-key-actions">
                <el-tag :type="site.server_side.access_key_set ? 'success' : 'info'" size="small">
                  {{ site.server_side.access_key_set ? '已配置密钥' : '未配置密钥' }}
                </el-tag>
                <el-button
                  size="small"
                  :disabled="!site._persistedId || keyWorkingId === site._persistedId"
                  :data-testid="`generate-key-${site.website_id || index}`"
                  @click="generateOrRotateKey(site)"
                >
                  {{ site.server_side.access_key_set ? '轮换密钥' : '生成密钥' }}
                </el-button>
                <el-button
                  v-if="site.server_side.access_key_set"
                  size="small"
                  type="danger"
                  plain
                  :disabled="keyWorkingId === site._persistedId"
                  @click="revokeKey(site)"
                >
                  吊销密钥
                </el-button>
              </div>
            </div>
            <div v-if="!site._persistedId" class="origin-hint">请先保存该站点后再生成服务端访问密钥。</div>

            <el-row :gutter="16" class="server-side-fields">
              <el-col :xs="24" :md="12">
                <el-form-item label="信任同命名空间用户名">
                  <div class="switch-row">
                    <el-switch v-model="site.server_side.trusted_username_namespace" />
                    <span class="switch-label">
                      {{ site.server_side.trusted_username_namespace ? '裸用户名可自动归并同名平台账号' : '仅按外部身份绑定，不按裸用户名归并' }}
                    </span>
                  </div>
                </el-form-item>
              </el-col>
            </el-row>

            <el-form-item label="宿主 OAuth Provider 映射（registration_id → DataAgent provider）">
              <div class="mapping-list">
                <div v-for="(entry, mi) in site.server_side.provider_mapping_rows" :key="mi" class="mapping-row">
                  <el-input v-model="entry.key" placeholder="宿主 registration_id，如 github" />
                  <span class="mapping-arrow">→</span>
                  <el-input v-model="entry.value" placeholder="DataAgent provider 名称，如 CorpSSO" />
                  <el-button text :icon="Close" class="origin-remove" @click="removeMapping(site, mi)" />
                </div>
                <el-button size="small" :icon="Plus" class="origin-add" @click="addMapping(site)">添加映射</el-button>
              </div>
            </el-form-item>
          </div>
        </el-form>
      </section>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Check, Close, Delete, Plus, QuestionFilled } from '@element-plus/icons-vue'
import { dataagentApi } from '@/api/dataagent'
import { copyText } from '@/utils/clipboard'

const loading = ref(false)
const saving = ref(false)
const sites = ref([])
const savedSnapshot = ref('[]')
const revealedKey = ref(null)
const keyWorkingId = ref('')

let keySeq = 0
const nextKey = () => `site_${++keySeq}`

const normalizeServerSide = (raw = {}) => {
  const ss = raw && typeof raw === 'object' ? raw : {}
  const mappings = ss.provider_mappings && typeof ss.provider_mappings === 'object' ? ss.provider_mappings : {}
  return {
    enabled: Boolean(ss.enabled),
    access_key_set: Boolean(ss.access_key_set || ss.enabled),
    trusted_username_namespace: Boolean(ss.trusted_username_namespace),
    provider_mapping_rows: Object.entries(mappings).map(([key, value]) => ({
      key: String(key || ''),
      value: String(value || '')
    }))
  }
}

const toPayloadServerSide = (ss = {}) => {
  const mappings = {}
  for (const row of ss.provider_mapping_rows || []) {
    const k = String(row?.key || '').trim()
    const v = String(row?.value || '').trim()
    if (k && v) mappings[k] = v
  }
  return {
    enabled: Boolean(ss.enabled),
    trusted_username_namespace: Boolean(ss.trusted_username_namespace),
    provider_mappings: mappings
  }
}

const normalizeSite = (raw = {}, persisted = false) => ({
  _key: nextKey(),
  _persistedId: persisted ? String(raw.website_id || '').trim() : '',
  website_id: String(raw.website_id || ''),
  project_name: String(raw.project_name || ''),
  project_color: String(raw.project_color || ''),
  allowed_origins: Array.isArray(raw.allowed_origins) ? raw.allowed_origins.map((item) => String(item || '')) : [],
  server_side: normalizeServerSide(raw.server_side)
})

const toPayloadSite = (site) => ({
  website_id: String(site.website_id || '').trim(),
  project_name: String(site.project_name || '').trim(),
  project_color: String(site.project_color || '').trim(),
  allowed_origins: (site.allowed_origins || [])
    .map((item) => String(item || '').trim())
    .filter((item, index, arr) => item && arr.indexOf(item) === index),
  server_side: toPayloadServerSide(site.server_side)
})

const payloadSites = computed(() => sites.value.map(toPayloadSite).filter((site) => site.website_id))

const snapshotOf = (list) => JSON.stringify(list)
const isDirty = computed(() => snapshotOf(payloadSites.value) !== savedSnapshot.value)

const applySettings = (payload) => {
  const list = Array.isArray(payload?.widget_allowed_sites) ? payload.widget_allowed_sites : []
  sites.value = list.map((item) => normalizeSite(item, true))
  savedSnapshot.value = snapshotOf(sites.value.map(toPayloadSite).filter((site) => site.website_id))
}

const loadSettings = async () => {
  loading.value = true
  try {
    const payload = await dataagentApi.getSettings()
    applySettings(payload)
  } finally {
    loading.value = false
  }
}

const addSite = () => {
  sites.value.push(normalizeSite({ allowed_origins: [''] }, false))
}

const removeSite = async (index) => {
  const site = sites.value[index]
  const name = String(site?.website_id || '').trim() || `站点 ${index + 1}`
  try {
    await ElMessageBox.confirm(`确认删除「${name}」吗？保存后该站点将无法再嵌入 Widget。`, '删除站点', {
      confirmButtonText: '删除',
      cancelButtonText: '取消',
      type: 'warning'
    })
  } catch {
    return
  }
  sites.value.splice(index, 1)
}

const addOrigin = (site) => {
  site.allowed_origins.push('')
}

const removeOrigin = (site, index) => {
  site.allowed_origins.splice(index, 1)
}

const addMapping = (site) => {
  site.server_side.provider_mapping_rows.push({ key: '', value: '' })
}

const removeMapping = (site, index) => {
  site.server_side.provider_mapping_rows.splice(index, 1)
}

const generateOrRotateKey = async (site) => {
  const wid = site._persistedId
  if (!wid) return
  if (site.server_side.access_key_set) {
    try {
      await ElMessageBox.confirm(
        `确认轮换站点「${wid}」的服务端访问密钥吗？旧密钥将立即失效。`,
        '轮换访问密钥',
        { confirmButtonText: '轮换', cancelButtonText: '取消', type: 'warning' }
      )
    } catch {
      return
    }
  }
  keyWorkingId.value = wid
  try {
    const res = await dataagentApi.createWidgetAccessKey(wid)
    revealedKey.value = res
    site.server_side.enabled = true
    site.server_side.access_key_set = true
    savedSnapshot.value = snapshotOf(payloadSites.value)
    ElMessage.success('访问密钥已生成')
  } catch (error) {
    if (!error?.__odwNotified) ElMessage.error(error?.message || '生成密钥失败')
  } finally {
    keyWorkingId.value = ''
  }
}

const revokeKey = async (site) => {
  const wid = site._persistedId
  if (!wid) return
  try {
    await ElMessageBox.confirm(`确认吊销站点「${wid}」的服务端访问密钥吗？`, '吊销访问密钥', {
      confirmButtonText: '吊销',
      cancelButtonText: '取消',
      type: 'warning'
    })
  } catch {
    return
  }
  keyWorkingId.value = wid
  try {
    await dataagentApi.deleteWidgetAccessKey(wid)
    site.server_side.enabled = false
    site.server_side.access_key_set = false
    if (revealedKey.value?.website_id === wid) revealedKey.value = null
    savedSnapshot.value = snapshotOf(payloadSites.value)
    ElMessage.success('访问密钥已吊销')
  } catch (error) {
    if (!error?.__odwNotified) ElMessage.error(error?.message || '吊销密钥失败')
  } finally {
    keyWorkingId.value = ''
  }
}

const copyKey = async (value) => {
  try {
    await copyText(value)
    ElMessage.success('已复制到剪贴板')
  } catch {
    ElMessage.error('复制失败，请手动复制')
  }
}

const validate = () => {
  const seen = new Set()
  for (const site of payloadSites.value) {
    if (seen.has(site.website_id)) {
      ElMessage.error(`Website ID 重复：${site.website_id}`)
      return false
    }
    seen.add(site.website_id)
  }
  if (sites.value.some((site) => !String(site.website_id || '').trim())) {
    ElMessage.error('存在未填写 Website ID 的站点，请补全或删除后再保存。')
    return false
  }
  return true
}

const save = async () => {
  if (!isDirty.value || !validate()) return
  saving.value = true
  try {
    const payload = await dataagentApi.updateSettings({ widget_allowed_sites: payloadSites.value })
    applySettings(payload)
    ElMessage.success('Widget 接入白名单已保存')
  } catch (error) {
    if (!error?.__odwNotified) {
      ElMessage.error(error?.message || '保存失败，请重试')
    }
  } finally {
    saving.value = false
  }
}

onMounted(loadSettings)
</script>

<style scoped>
.widget-access {
  color: #1f2937;
  display: flex;
  flex-direction: column;
  gap: 18px;
}

.page-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 16px;
  padding: 18px 20px;
  border: 1px solid #cddceb;
  border-left: 4px solid #1f5f99;
  border-radius: 8px;
  background: linear-gradient(90deg, #f4f8fc 0%, #ffffff 72%);
}

.page-header-main {
  min-width: 0;
}

.page-title {
  margin: 7px 0 6px;
  font-size: 22px;
  font-weight: 700;
  color: #16324f;
}

.page-desc {
  margin: 0;
  max-width: 720px;
  font-size: 13px;
  line-height: 1.7;
  color: #53677e;
}

.page-desc code,
.field-label code,
.server-side-desc code {
  padding: 1px 5px;
  border-radius: 4px;
  background: #eef3f9;
  font-size: 12px;
  color: #1f5f99;
}

.page-header-actions {
  display: inline-flex;
  gap: 10px;
  flex: none;
}

.page-header-actions :deep(.el-button--primary) {
  --el-button-bg-color: #1f5f99;
  --el-button-border-color: #1f5f99;
  --el-button-hover-bg-color: #2c74b8;
  --el-button-hover-border-color: #2c74b8;
  --el-button-active-bg-color: #184d7d;
  --el-button-active-border-color: #184d7d;
}

.key-alert {
  border-radius: 8px;
}

.key-reveal-row {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 8px;
  flex-wrap: wrap;
}

.key-code {
  padding: 4px 8px;
  border-radius: 4px;
  background: #fffbeb;
  border: 1px solid #fcd34d;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 12px;
  color: #92400e;
  word-break: break-all;
}

.empty-block {
  padding: 48px 20px;
  border: 1px dashed #b7cbe1;
  border-radius: 8px;
  background: #f7faff;
  text-align: center;
}

.empty-title {
  font-size: 15px;
  font-weight: 700;
  color: #16324f;
}

.empty-desc {
  margin-top: 6px;
  font-size: 13px;
  color: #53677e;
}

.site-list {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.site-card {
  padding: 18px 20px;
  border: 1px solid #d8e3ef;
  border-radius: 8px;
  background: #ffffff;
}

.site-card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 14px;
}

.site-index {
  font-size: 14px;
  font-weight: 700;
  color: #16324f;
}

.site-remove {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  border: none;
  background: none;
  color: #b42318;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
}

.site-remove:hover {
  color: #912018;
}

.field-label {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.field-label .el-icon {
  color: #71839a;
}

.site-form :deep(.el-form-item) {
  margin-bottom: 16px;
}

.color-row {
  display: inline-flex;
  align-items: center;
  gap: 12px;
}

.color-input {
  width: 160px;
}

.origin-list,
.mapping-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
  width: 100%;
}

.origin-row,
.mapping-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.mapping-arrow {
  color: #64748b;
  flex: none;
}

.origin-remove {
  flex: none;
  color: #98a6b8;
}

.origin-remove:hover {
  color: #b42318;
}

.origin-add {
  align-self: flex-start;
}

.origin-hint {
  font-size: 12px;
  color: #8090a3;
}

.server-side-box {
  margin-top: 8px;
  padding: 14px 16px;
  border: 1px solid #e2e8f0;
  border-radius: 6px;
  background: #f8fafc;
}

.server-side-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
  flex-wrap: wrap;
}

.server-side-title {
  font-size: 14px;
  font-weight: 600;
  color: #0f172a;
}

.server-side-desc {
  margin-top: 2px;
  font-size: 12px;
  color: #64748b;
}

.server-side-key-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.server-side-fields {
  margin-top: 14px;
}

.switch-row {
  display: inline-flex;
  align-items: center;
  gap: 10px;
}

.switch-label {
  font-size: 12px;
  color: #53677e;
}

@media (max-width: 768px) {
  .page-header {
    flex-direction: column;
  }

  .page-header-actions {
    width: 100%;
  }

  .page-header-actions .el-button {
    flex: 1;
  }
}
</style>
