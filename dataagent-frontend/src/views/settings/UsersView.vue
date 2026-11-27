<template>
  <div class="users-view">
    <div class="users-view__toolbar">
      <div>
        <div class="users-view__title">用户管理</div>
        <div class="users-view__subtitle">
          管理平台账号的状态和所属角色。停用账号会保留历史会话。
        </div>
      </div>
      <div class="users-view__actions">
        <el-input
          v-model="keyword"
          placeholder="搜索用户名或显示名"
          clearable
          class="users-view__search"
          data-testid="users-search"
          @keyup.enter="handleSearch"
          @clear="handleSearch"
        />
        <el-select
          v-model="statusFilter"
          placeholder="全部状态"
          class="users-view__status"
          data-testid="users-status-filter"
          @change="handleSearch"
        >
          <el-option label="全部状态" value="all" />
          <el-option label="已启用" value="active" />
          <el-option label="已停用" value="disabled" />
        </el-select>
        <el-button :icon="Refresh" @click="loadUsers">刷新</el-button>
        <el-button type="primary" :icon="Plus" data-testid="open-create-user" @click="openCreateDialog">
          新建用户
        </el-button>
      </div>
    </div>

    <el-table v-loading="loading" :data="users" stripe class="users-view__table" empty-text="暂无用户">
      <el-table-column prop="username" label="用户名" min-width="130" show-overflow-tooltip />
      <el-table-column label="显示名" min-width="130" show-overflow-tooltip>
        <template #default="{ row }">{{ row.display_name || '—' }}</template>
      </el-table-column>
      <el-table-column label="角色" min-width="160">
        <template #default="{ row }">
          <div class="tag-list">
            <el-tag
              v-for="role in row.roles || []"
              :key="role.id"
              size="small"
              :type="role.role_key === 'admin' ? 'danger' : 'info'"
            >
              {{ role.name || role.role_key }}
            </el-tag>
            <span v-if="!(row.roles && row.roles.length)" class="muted">—</span>
          </div>
        </template>
      </el-table-column>
      <el-table-column label="状态" width="90">
        <template #default="{ row }">
          <el-tag :type="row.is_active ? 'success' : 'info'" size="small">
            {{ row.is_active ? '启用' : '已停用' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="最近登录" width="165">
        <template #default="{ row }">{{ formatTime(row.last_login_at) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="230" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" size="small" :data-testid="`edit-user-${row.id}`" @click="openEditDialog(row)">
            编辑
          </el-button>
          <el-button link type="warning" size="small" @click="confirmResetPassword(row)">重置密码</el-button>
          <el-button
            link
            :type="row.is_active ? 'danger' : 'success'"
            size="small"
            :data-testid="`toggle-user-${row.id}`"
            @click="confirmToggleActive(row)"
          >
            {{ row.is_active ? '停用' : '启用' }}
          </el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-pagination
      v-if="total > pageSize"
      v-model:current-page="page"
      :page-size="pageSize"
      :total="total"
      layout="prev, pager, next, total"
      class="users-view__pager"
      @current-change="loadUsers"
    />

    <!-- 新建用户 -->
    <el-dialog v-model="createVisible" title="新建用户" width="480px" destroy-on-close>
      <el-alert
        type="info"
        :closable="false"
        show-icon
        title="新用户将使用系统默认初始密码创建，请提醒用户在首次登录后修改密码。"
        class="dialog-alert"
      />
      <el-form :model="createForm" label-position="top">
        <el-form-item label="用户名" required>
          <el-input v-model="createForm.username" placeholder="小写字母/数字/._-，例如 zhangsan" />
        </el-form-item>
        <el-form-item label="显示名">
          <el-input v-model="createForm.display_name" placeholder="例如 张三（留空默认同用户名）" />
        </el-form-item>
        <el-form-item label="角色">
          <el-select v-model="createForm.role_ids" multiple placeholder="默认分配普通用户角色" style="width: 100%">
            <el-option
              v-for="role in roles"
              :key="role.id"
              :label="`${role.name} (${role.role_key})`"
              :value="role.id"
              :disabled="role.role_key === 'admin'"
            />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" data-testid="submit-create-user" @click="submitCreate">
          创建
        </el-button>
      </template>
    </el-dialog>

    <!-- 编辑用户 -->
    <el-dialog v-model="editVisible" title="编辑用户" width="480px" destroy-on-close>
      <el-form :model="editForm" label-position="top">
        <el-form-item label="用户名">
          <el-input :model-value="editForm.username" disabled />
        </el-form-item>
        <el-form-item label="显示名">
          <el-input v-model="editForm.display_name" placeholder="显示名" />
        </el-form-item>
        <el-form-item label="角色">
          <el-select v-model="editForm.role_ids" multiple placeholder="选择角色" style="width: 100%">
            <el-option
              v-for="role in roles"
              :key="role.id"
              :label="`${role.name} (${role.role_key})`"
              :value="role.id"
              :disabled="role.role_key === 'admin'"
            />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" data-testid="submit-edit-user" @click="submitEdit">
          保存
        </el-button>
      </template>
    </el-dialog>

  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Refresh } from '@element-plus/icons-vue'
import { dataagentApi } from '@/api/dataagent'
import { formatDateTime } from '@/utils/time'

const pageSize = 20
const users = ref([])
const roles = ref([])
const total = ref(0)
const page = ref(1)
const keyword = ref('')
const statusFilter = ref('all')
const loading = ref(false)
const submitting = ref(false)

const createVisible = ref(false)
const createForm = reactive({ username: '', display_name: '', role_ids: [] })

const editVisible = ref(false)
const editForm = reactive({ id: null, username: '', display_name: '', role_ids: [] })


const formatTime = (value) => formatDateTime(value)

async function loadRoles() {
  try {
    const data = await dataagentApi.listRoles()
    roles.value = Array.isArray(data?.items) ? data.items : (Array.isArray(data) ? data : [])
  } catch {
    roles.value = []
  }
}

async function loadUsers() {
  loading.value = true
  try {
    const params = { page: page.value, page_size: pageSize }
    if (keyword.value.trim()) params.keyword = keyword.value.trim()
    if (statusFilter.value === 'active') params.is_active = true
    if (statusFilter.value === 'disabled') params.is_active = false
    const data = await dataagentApi.listUsers(params)
    users.value = Array.isArray(data?.items) ? data.items : []
    total.value = Number(data?.total || 0)
  } catch {
    users.value = []
  } finally {
    loading.value = false
  }
}

function handleSearch() {
  page.value = 1
  loadUsers()
}

function openCreateDialog() {
  const defaultUserRole = roles.value.find((r) => r.role_key === 'user')
  createForm.username = ''
  createForm.display_name = ''
  createForm.role_ids = defaultUserRole ? [defaultUserRole.id] : []
  createVisible.value = true
}

async function submitCreate() {
  if (!createForm.username.trim()) {
    ElMessage.error('请输入用户名')
    return
  }
  submitting.value = true
  try {
    await dataagentApi.createUser({
      username: createForm.username.trim(),
      display_name: createForm.display_name.trim(),
      role_ids: createForm.role_ids
    })
    ElMessage.success('用户已创建（使用默认初始密码）')
    createVisible.value = false
    await loadUsers()
  } catch (error) {
    if (!error?.__odwNotified) ElMessage.error(error?.message || '创建用户失败')
  } finally {
    submitting.value = false
  }
}

function openEditDialog(row) {
  editForm.id = row.id
  editForm.username = row.username
  editForm.display_name = row.display_name || ''
  editForm.role_ids = (row.roles || []).map((r) => r.id)
  editVisible.value = true
}

async function submitEdit() {
  submitting.value = true
  try {
    await dataagentApi.updateUser(editForm.id, {
      display_name: editForm.display_name.trim(),
      role_ids: editForm.role_ids
    })
    ElMessage.success('用户信息已更新')
    editVisible.value = false
    await loadUsers()
  } catch (error) {
    if (!error?.__odwNotified) ElMessage.error(error?.message || '更新用户失败')
  } finally {
    submitting.value = false
  }
}

async function confirmToggleActive(row) {
  const targetActive = !row.is_active
  const action = targetActive ? '启用' : '停用'
  try {
    await ElMessageBox.confirm(
      targetActive
        ? `确认重新启用用户「${row.display_name || row.username}」吗？`
        : `确认停用用户「${row.display_name || row.username}」吗？停用后该用户会话与访问令牌将立即失效。`,
      `${action}用户`,
      { confirmButtonText: action, cancelButtonText: '取消', type: 'warning' }
    )
  } catch {
    return
  }
  try {
    await dataagentApi.updateUser(row.id, { is_active: targetActive })
    ElMessage.success(`用户已${action}`)
    await loadUsers()
  } catch (error) {
    if (!error?.__odwNotified) ElMessage.error(error?.message || `${action}失败`)
  }
}

async function confirmResetPassword(row) {
  try {
    await ElMessageBox.confirm(
      `确认将用户「${row.display_name || row.username}」的密码重置为系统默认初始密码吗？`,
      '重置密码',
      { confirmButtonText: '重置', cancelButtonText: '取消', type: 'warning' }
    )
  } catch {
    return
  }
  try {
    await dataagentApi.resetUserPassword(row.id)
    ElMessage.success('密码已重置为系统默认初始密码')
  } catch (error) {
    if (!error?.__odwNotified) ElMessage.error(error?.message || '重置密码失败')
  }
}

onMounted(() => {
  loadRoles()
  loadUsers()
})
</script>

<style scoped>
.users-view {
  display: flex;
  flex-direction: column;
  gap: 16px;
  color: #1f2937;
}
.users-view__toolbar {
  display: flex;
  justify-content: space-between;
  align-items: flex-end;
  gap: 16px;
  flex-wrap: wrap;
}
.users-view__title {
  font-size: 18px;
  font-weight: 600;
  color: #0f172a;
}
.users-view__subtitle {
  margin-top: 4px;
  font-size: 13px;
  color: #64748b;
}
.users-view__actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.users-view__search {
  width: 220px;
}
.users-view__status {
  width: 115px;
}
.users-view__table {
  width: 100%;
  border-radius: 8px;
  overflow: hidden;
}
.users-view__pager {
  justify-content: flex-end;
}
.tag-list {
  display: flex;
  gap: 4px;
  flex-wrap: wrap;
}
.muted {
  color: #94a3b8;
}
.dialog-alert {
  margin-bottom: 16px;
}
</style>
