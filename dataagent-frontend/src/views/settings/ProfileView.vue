<template>
  <div v-loading="loading" class="profile-view">
    <div class="profile-view__header">
      <div class="profile-view__title">个人资料</div>
      <div class="profile-view__subtitle">管理个人信息和登录密码，查看所属角色。</div>
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
          <el-form-item label="登录方式">
            <span>{{ loginMethodLabel }}</span>
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
        <span class="card-title">所属角色</span>
      </template>

      <div class="readonly-section">
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
    </el-card>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { dataagentApi } from '@/api/dataagent'
import { useAuthStore } from '@/stores/auth'

const authStore = useAuthStore()

const loading = ref(false)
const savingProfile = ref(false)
const savingPassword = ref(false)
const profile = ref({
  username: '',
  display_name: '',
  roles: [],
  login_method: ''
})

const loginMethodLabel = computed(() => {
  if (profile.value.login_method === 'oauth') return 'OAuth'
  if (profile.value.login_method === 'password') return '用户名和密码'
  return '—'
})

const profileForm = reactive({ display_name: '' })
const passwordForm = reactive({ current_password: '', new_password: '', confirm_password: '' })

async function loadProfile() {
  loading.value = true
  try {
    const data = await dataagentApi.getProfile()
    profile.value = data || {}
    profileForm.display_name = String(data?.display_name || '')
  } finally {
    loading.value = false
  }
}

async function saveProfile() {
  savingProfile.value = true
  try {
    const updated = await dataagentApi.updateProfile({
      display_name: profileForm.display_name.trim()
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
.readonly-section {
  margin-bottom: 14px;
}
.readonly-section:last-child {
  margin-bottom: 0;
}
.tag-list {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}
.muted {
  color: #94a3b8;
  font-size: 13px;
}
</style>
