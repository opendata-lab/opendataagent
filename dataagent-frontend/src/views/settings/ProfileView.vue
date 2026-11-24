<template>
  <div v-loading="loading" class="profile-view">
    <div class="profile-view__header">
      <div class="profile-view__title">个人资料</div>
      <div class="profile-view__subtitle">管理个人信息、登录密码，并查看您的角色与可用智能体。</div>
    </div>

    <div class="profile-grid">
      <el-card shadow="never" class="profile-card">
        <template #header>
          <span class="card-title">基本信息</span>
        </template>
        <el-form :model="profileForm" label-position="top">
          <el-form-item label="用户名">
            <el-input :model-value="profile.username" disabled />
          </el-form-item>
          <el-form-item label="显示名">
            <el-input v-model="profileForm.display_name" placeholder="请输入显示名" data-testid="profile-display-name" />
          </el-form-item>
          <el-form-item>
            <template #label>
              <span>邮箱</span>
              <el-tag v-if="profile.email" size="small" :type="profile.email_verified ? 'success' : 'info'" class="inline-tag">
                {{ profile.email_verified ? '已验证' : '未验证' }}
              </el-tag>
            </template>
            <el-input v-model="profileForm.email" placeholder="可选" data-testid="profile-email" />
            <div class="field-hint">手工修改的邮箱会保存为未验证状态。</div>
          </el-form-item>
          <el-button type="primary" :loading="savingProfile" data-testid="save-profile" @click="saveProfile">
            保存资料
          </el-button>
        </el-form>
      </el-card>

      <el-card shadow="never" class="profile-card">
        <template #header>
          <span class="card-title">修改密码</span>
        </template>
        <el-form :model="passwordForm" label-position="top">
          <el-form-item label="当前密码">
            <el-input v-model="passwordForm.current_password" type="password" show-password autocomplete="current-password" />
          </el-form-item>
          <el-form-item label="新密码（至少 6 位）">
            <el-input v-model="passwordForm.new_password" type="password" show-password autocomplete="new-password" />
          </el-form-item>
          <el-form-item label="确认新密码">
            <el-input v-model="passwordForm.confirm_password" type="password" show-password autocomplete="new-password" />
          </el-form-item>
          <el-button type="primary" :loading="savingPassword" data-testid="save-password" @click="savePassword">
            更新密码
          </el-button>
        </el-form>
      </el-card>
    </div>

    <el-card shadow="never" class="profile-card">
      <template #header>
        <span class="card-title">角色、身份与可用智能体</span>
      </template>

      <div class="readonly-section">
        <div class="readonly-section__label">所属角色</div>
        <div class="tag-list">
          <el-tag
            v-for="role in profile.roles || []"
            :key="role.id"
            size="small"
            :type="role.role_key === 'admin' ? 'danger' : 'primary'"
          >
            {{ role.name || role.role_key }}
          </el-tag>
          <span v-if="!(profile.roles && profile.roles.length)" class="muted">—</span>
        </div>
      </div>

      <div class="readonly-section">
        <div class="readonly-section__label">已绑定外部身份</div>
        <div class="tag-list">
          <el-tag v-for="ident in profile.identities || []" :key="ident.id" size="small" type="info">
            {{ ident.source }}:{{ ident.subject }}
          </el-tag>
          <span v-if="!(profile.identities && profile.identities.length)" class="muted">未绑定外部身份</span>
        </div>
      </div>

      <div class="readonly-section">
        <div class="readonly-section__label">可见菜单</div>
        <div class="tag-list">
          <el-tag v-for="menu in profile.menus || []" :key="menu" size="small" type="success">
            {{ MENU_LABELS[menu] || menu }}
          </el-tag>
        </div>
      </div>

      <div class="readonly-section">
        <div class="readonly-section__label">可用智能体（{{ (profile.accessible_agents || []).length }}）</div>
        <div class="agent-chips">
          <el-button
            v-for="agent in profile.accessible_agents || []"
            :key="agent.agent_id"
            size="small"
            plain
            @click="router.push({ path: '/chat', query: { agent_id: agent.agent_id } })"
          >
            {{ agent.name || agent.agent_id }}
          </el-button>
          <span v-if="!(profile.accessible_agents && profile.accessible_agents.length)" class="muted">暂无可用智能体</span>
        </div>
      </div>
    </el-card>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { dataagentApi } from '@/api/dataagent'
import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const authStore = useAuthStore()

const MENU_LABELS = {
  'menu:chat': 'Chat 对话',
  'menu:agents': '智能体',
  'menu:skills': 'Skills',
  'menu:mcp': 'MCP 服务',
  'menu:mcp_tokens': '访问令牌',
  'menu:profile': '个人资料'
}

const loading = ref(false)
const savingProfile = ref(false)
const savingPassword = ref(false)
const profile = ref({
  username: '',
  display_name: '',
  email: '',
  email_verified: false,
  roles: [],
  identities: [],
  menus: [],
  accessible_agents: []
})

const profileForm = reactive({ display_name: '', email: '' })
const passwordForm = reactive({ current_password: '', new_password: '', confirm_password: '' })

async function loadProfile() {
  loading.value = true
  try {
    const data = await dataagentApi.getProfile()
    profile.value = data || {}
    profileForm.display_name = String(data?.display_name || '')
    profileForm.email = String(data?.email || '')
  } finally {
    loading.value = false
  }
}

async function saveProfile() {
  savingProfile.value = true
  try {
    const updated = await dataagentApi.updateProfile({
      display_name: profileForm.display_name.trim(),
      email: profileForm.email.trim() || null
    })
    profile.value = { ...profile.value, ...updated }
    await authStore.refreshMe()
    ElMessage.success('个人资料已保存')
  } catch (error) {
    if (!error?.__odwNotified) ElMessage.error(error?.message || '保存失败')
  } finally {
    savingProfile.value = false
  }
}

async function savePassword() {
  if (!passwordForm.current_password || !passwordForm.new_password) {
    ElMessage.error('请填写当前密码与新密码')
    return
  }
  if (passwordForm.new_password.length < 6) {
    ElMessage.error('新密码长度至少为 6 位')
    return
  }
  if (passwordForm.new_password !== passwordForm.confirm_password) {
    ElMessage.error('两次输入的新密码不一致')
    return
  }
  savingPassword.value = true
  try {
    await dataagentApi.changePassword({
      current_password: passwordForm.current_password,
      new_password: passwordForm.new_password
    })
    passwordForm.current_password = ''
    passwordForm.new_password = ''
    passwordForm.confirm_password = ''
    ElMessage.success('密码已更新')
  } catch (error) {
    if (!error?.__odwNotified) ElMessage.error(error?.message || '修改密码失败')
  } finally {
    savingPassword.value = false
  }
}

onMounted(loadProfile)
</script>

<style scoped>
.profile-view {
  display: flex;
  flex-direction: column;
  gap: 16px;
  color: #1f2937;
}
.profile-view__title {
  font-size: 18px;
  font-weight: 600;
  color: #0f172a;
}
.profile-view__subtitle {
  margin-top: 4px;
  font-size: 13px;
  color: #64748b;
}
.profile-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
  gap: 16px;
}
.profile-card {
  border-radius: 8px;
}
.card-title {
  font-weight: 600;
  color: #0f172a;
}
.inline-tag {
  margin-left: 6px;
}
.field-hint {
  margin-top: 4px;
  font-size: 12px;
  color: #94a3b8;
}
.readonly-section {
  margin-bottom: 14px;
}
.readonly-section:last-child {
  margin-bottom: 0;
}
.readonly-section__label {
  font-size: 13px;
  font-weight: 600;
  color: #334155;
  margin-bottom: 8px;
}
.tag-list,
.agent-chips {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}
.muted {
  color: #94a3b8;
  font-size: 13px;
}
</style>
