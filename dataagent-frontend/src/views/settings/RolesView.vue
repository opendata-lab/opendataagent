<template>
  <div class="roles-view">
    <div class="roles-view__toolbar">
      <div>
        <div class="roles-view__title">角色管理</div>
        <div class="roles-view__subtitle">
          内置角色（管理员、普通用户）承载静态菜单与管理权限；自定义角色用于按团队或岗位向智能体批量授权。
        </div>
      </div>
      <div class="roles-view__actions">
        <el-button :icon="Refresh" @click="loadAll">刷新</el-button>
        <el-button type="primary" :icon="Plus" data-testid="open-create-role" @click="openCreateDialog">
          新建角色
        </el-button>
      </div>
    </div>

    <el-table v-loading="loading" :data="roles" stripe class="roles-view__table" empty-text="暂无角色">
      <el-table-column prop="role_key" label="角色标识" width="150" />
      <el-table-column prop="name" label="角色名称" min-width="140" />
      <el-table-column label="类型" width="90">
        <template #default="{ row }">
          <el-tag :type="row.is_builtin ? 'danger' : 'primary'" size="small">
            {{ row.is_builtin ? '内置' : '自定义' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="说明" min-width="200" show-overflow-tooltip>
        <template #default="{ row }">{{ row.description || '—' }}</template>
      </el-table-column>
      <el-table-column label="成员" width="90">
        <template #default="{ row }">{{ row.member_count ?? 0 }} 人</template>
      </el-table-column>
      <el-table-column label="助手授权" width="100">
        <template #default="{ row }">
          <span v-if="row.role_key === 'admin'" class="muted">全部助手</span>
          <span v-else>{{ (row.agent_ids || []).length }} 个</span>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="290" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" size="small" @click="openPermissionsDialog(row)">权限说明</el-button>
          <el-button
            v-if="row.role_key !== 'admin'"
            link
            type="primary"
            size="small"
            :data-testid="`role-members-${row.id}`"
            @click="openMembersDialog(row)"
          >
            成员
          </el-button>
          <el-button
            v-if="row.role_key !== 'admin'"
            link
            type="primary"
            size="small"
            :data-testid="`role-agents-${row.id}`"
            @click="openAgentsDialog(row)"
          >
            助手授权
          </el-button>
          <el-button v-if="!row.is_builtin" link type="primary" size="small" @click="openEditDialog(row)">
            编辑
          </el-button>
          <el-button v-if="!row.is_builtin" link type="danger" size="small" @click="confirmDeleteRole(row)">
            删除
          </el-button>
        </template>
      </el-table-column>
    </el-table>

    <!-- 新建自定义角色 -->
    <el-dialog v-model="createVisible" title="新建自定义角色" width="460px" destroy-on-close>
      <el-form :model="createForm" label-position="top">
        <el-form-item label="角色标识 (role_key)" required>
          <el-input v-model="createForm.role_key" placeholder="小写字母/数字/_/-，例如 analyst" />
        </el-form-item>
        <el-form-item label="角色名称" required>
          <el-input v-model="createForm.name" placeholder="例如 数据分析师" />
        </el-form-item>
        <el-form-item label="说明">
          <el-input v-model="createForm.description" type="textarea" :rows="2" placeholder="可选" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" data-testid="submit-create-role" @click="submitCreate">
          创建
        </el-button>
      </template>
    </el-dialog>

    <!-- 编辑角色 -->
    <el-dialog v-model="editVisible" title="编辑角色" width="460px" destroy-on-close>
      <el-form :model="editForm" label-position="top">
        <el-form-item label="角色标识">
          <el-input :model-value="editForm.role_key" disabled />
        </el-form-item>
        <el-form-item label="角色名称" required>
          <el-input v-model="editForm.name" />
        </el-form-item>
        <el-form-item label="说明">
          <el-input v-model="editForm.description" type="textarea" :rows="2" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="submitEdit">保存</el-button>
      </template>
    </el-dialog>

    <!-- 静态权限说明（只读） -->
    <el-dialog
      v-model="permissionsVisible"
      :title="`静态权限说明 · ${activeRole?.name || ''}`"
      width="560px"
      destroy-on-close
    >
      <el-alert
        type="info"
        :closable="false"
        show-icon
        title="菜单与管理权限为代码内置静态集合（只读），不支持动态勾选。自定义角色不携带额外管理权限，仅用于智能体授权。"
        class="dialog-alert"
      />
      <div class="perm-section">
        <div class="perm-section__title">可见菜单</div>
        <div class="tag-list">
          <el-tag v-for="key in activeRole?.menus || []" :key="key" size="small" type="success">
            {{ permissionLabel(key) }}
          </el-tag>
          <span v-if="!(activeRole?.menus && activeRole.menus.length)" class="muted">无额外静态菜单（继承用户已分配的其他角色）</span>
        </div>
      </div>
      <div class="perm-section">
        <div class="perm-section__title">权限键列表</div>
        <div class="tag-list">
          <el-tag v-for="key in activeRole?.permissions || []" :key="key" size="small" type="info">
            {{ permissionLabel(key) }}（{{ key }}）
          </el-tag>
          <span v-if="!(activeRole?.permissions && activeRole.permissions.length)" class="muted">无静态权限键</span>
        </div>
      </div>
    </el-dialog>

    <!-- 角色成员管理 -->
    <el-dialog
      v-model="membersVisible"
      :title="`角色成员 · ${activeRole?.name || ''}`"
      width="520px"
      destroy-on-close
    >
      <el-select
        v-model="selectedMemberIds"
        multiple
        filterable
        placeholder="选择属于该角色的用户"
        style="width: 100%"
      >
        <el-option
          v-for="user in allUsers"
          :key="user.id"
          :label="user.display_name ? `${user.display_name} (${user.username})` : user.username"
          :value="user.id"
        />
      </el-select>
      <template #footer>
        <el-button @click="membersVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" data-testid="save-role-members" @click="saveMembers">
          保存成员
        </el-button>
      </template>
    </el-dialog>

    <!-- 角色助手授权 -->
    <el-dialog
      v-model="agentsVisible"
      :title="`助手授权 · ${activeRole?.name || ''}`"
      width="520px"
      destroy-on-close
    >
      <p class="dialog-hint">
        选择允许该角色访问的智能体（当智能体的可见范围为「指定用户或角色」时生效）。保存后立即生效。
      </p>
      <el-select
        v-model="selectedAgentIds"
        multiple
        filterable
        placeholder="选择授权的智能体"
        style="width: 100%"
      >
        <el-option
          v-for="agent in allAgents"
          :key="agent.agent_id"
          :label="`${agent.name} (${agent.agent_id})`"
          :value="agent.agent_id"
        />
      </el-select>
      <template #footer>
        <el-button @click="agentsVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" data-testid="save-role-agents" @click="saveAgents">
          保存授权
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

const roles = ref([])
const permissionCatalog = ref([])
const allUsers = ref([])
const allAgents = ref([])
const loading = ref(false)
const submitting = ref(false)

const activeRole = ref(null)
const createVisible = ref(false)
const createForm = reactive({ role_key: '', name: '', description: '' })

const editVisible = ref(false)
const editForm = reactive({ id: null, role_key: '', name: '', description: '' })

const permissionsVisible = ref(false)
const membersVisible = ref(false)
const selectedMemberIds = ref([])

const agentsVisible = ref(false)
const selectedAgentIds = ref([])

const permissionLabel = (key) => {
  const found = permissionCatalog.value.find((item) => item.key === key)
  return found?.label || key
}

async function loadAll() {
  loading.value = true
  try {
    const [rolesRes, permRes] = await Promise.all([
      dataagentApi.listRoles(),
      dataagentApi.listRolePermissions().catch(() => ({ items: [] }))
    ])
    roles.value = Array.isArray(rolesRes?.items) ? rolesRes.items : (Array.isArray(rolesRes) ? rolesRes : [])
    permissionCatalog.value = Array.isArray(permRes?.items) ? permRes.items : []
  } catch {
    roles.value = []
  } finally {
    loading.value = false
  }
}

function openCreateDialog() {
  createForm.role_key = ''
  createForm.name = ''
  createForm.description = ''
  createVisible.value = true
}

async function submitCreate() {
  if (!createForm.role_key.trim() || !createForm.name.trim()) {
    ElMessage.error('请填写角色标识与角色名称')
    return
  }
  submitting.value = true
  try {
    await dataagentApi.createRole({
      role_key: createForm.role_key.trim(),
      name: createForm.name.trim(),
      description: createForm.description.trim()
    })
    ElMessage.success('角色已创建')
    createVisible.value = false
    await loadAll()
  } catch (error) {
    if (!error?.__odwNotified) ElMessage.error(error?.message || '创建角色失败')
  } finally {
    submitting.value = false
  }
}

function openEditDialog(row) {
  editForm.id = row.id
  editForm.role_key = row.role_key
  editForm.name = row.name
  editForm.description = row.description || ''
  editVisible.value = true
}

async function submitEdit() {
  if (!editForm.name.trim()) {
    ElMessage.error('请输入角色名称')
    return
  }
  submitting.value = true
  try {
    await dataagentApi.updateRole(editForm.id, {
      name: editForm.name.trim(),
      description: editForm.description.trim()
    })
    ElMessage.success('角色已更新')
    editVisible.value = false
    await loadAll()
  } catch (error) {
    if (!error?.__odwNotified) ElMessage.error(error?.message || '更新角色失败')
  } finally {
    submitting.value = false
  }
}

async function confirmDeleteRole(row) {
  try {
    await ElMessageBox.confirm(`确认删除自定义角色「${row.name}」吗？该角色的成员归属和助手授权将一并移除。`, '删除角色', {
      confirmButtonText: '删除',
      cancelButtonText: '取消',
      type: 'warning'
    })
  } catch {
    return
  }
  try {
    await dataagentApi.deleteRole(row.id)
    ElMessage.success('角色已删除')
    await loadAll()
  } catch (error) {
    if (!error?.__odwNotified) ElMessage.error(error?.message || '删除角色失败')
  }
}

function openPermissionsDialog(row) {
  activeRole.value = row
  permissionsVisible.value = true
}

async function openMembersDialog(row) {
  activeRole.value = row
  membersVisible.value = true
  try {
    const [membersRes, usersRes] = await Promise.all([
      dataagentApi.listRoleUsers(row.id),
      dataagentApi.listUsers({ page: 1, page_size: 200 })
    ])
    const members = Array.isArray(membersRes?.items) ? membersRes.items : []
    selectedMemberIds.value = members.map((u) => u.id)
    allUsers.value = Array.isArray(usersRes?.items) ? usersRes.items : []
  } catch {
    selectedMemberIds.value = []
  }
}

async function saveMembers() {
  if (!activeRole.value) return
  submitting.value = true
  try {
    await dataagentApi.setRoleUsers(activeRole.value.id, selectedMemberIds.value)
    ElMessage.success('角色成员已更新')
    membersVisible.value = false
    await loadAll()
  } catch (error) {
    if (!error?.__odwNotified) ElMessage.error(error?.message || '保存成员失败')
  } finally {
    submitting.value = false
  }
}

async function openAgentsDialog(row) {
  activeRole.value = row
  selectedAgentIds.value = [...(row.agent_ids || [])]
  agentsVisible.value = true
  try {
    const list = await dataagentApi.listAgentWorkbench()
    allAgents.value = Array.isArray(list) ? list : (list?.items || [])
  } catch {
    allAgents.value = []
  }
}

async function saveAgents() {
  if (!activeRole.value) return
  submitting.value = true
  try {
    await dataagentApi.setRoleAgents(activeRole.value.id, selectedAgentIds.value)
    ElMessage.success('角色助手授权已更新')
    agentsVisible.value = false
    await loadAll()
  } catch (error) {
    if (!error?.__odwNotified) ElMessage.error(error?.message || '保存授权失败')
  } finally {
    submitting.value = false
  }
}

onMounted(loadAll)
</script>

<style scoped>
.roles-view {
  display: flex;
  flex-direction: column;
  gap: 16px;
  color: #1f2937;
}
.roles-view__toolbar {
  display: flex;
  justify-content: space-between;
  align-items: flex-end;
  gap: 16px;
  flex-wrap: wrap;
}
.roles-view__title {
  font-size: 18px;
  font-weight: 600;
  color: #0f172a;
}
.roles-view__subtitle {
  margin-top: 4px;
  font-size: 13px;
  color: #64748b;
}
.roles-view__actions {
  display: flex;
  gap: 8px;
}
.roles-view__table {
  width: 100%;
  border-radius: 8px;
  overflow: hidden;
}
.muted {
  color: #94a3b8;
}
.dialog-alert {
  margin-bottom: 14px;
}
.dialog-hint {
  margin: 0 0 12px;
  font-size: 13px;
  color: #64748b;
}
.perm-section {
  margin-bottom: 14px;
}
.perm-section__title {
  font-size: 13px;
  font-weight: 600;
  color: #334155;
  margin-bottom: 8px;
}
.tag-list {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}
</style>
