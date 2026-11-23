<template>
  <main class="consent-page">
    <el-card class="consent-card">
      <h1>MCP 授权</h1>
      <template v-if="standalone && authStore.currentUser && clientName && callbackHost">
        <p>允许此客户端代你使用有权访问且已开启 MCP 的问数助手吗？</p>
        <dl>
          <dt>客户端</dt><dd><bdi>{{ clientName }}</bdi></dd>
          <dt>回调主机</dt><dd><bdi>{{ callbackHost }}</bdi></dd>
          <dt>当前用户</dt><dd><bdi>{{ authStore.currentUser.display_name }}</bdi></dd>
        </dl>
        <p v-if="errorMessage" role="alert">{{ errorMessage }}</p>
        <div class="consent-actions">
          <el-button :disabled="submitting" @click="decide('deny')">拒绝</el-button>
          <el-button type="primary" :disabled="submitting" @click="decide('allow')">允许</el-button>
        </div>
      </template>
      <p v-else role="alert">无法处理此授权请求，请从 MCP 客户端重新发起。</p>
    </el-card>
  </main>
</template>

<script setup>
import { computed, ref } from 'vue'
import { useRoute } from 'vue-router'
import { dataagentApi } from '@/api/dataagent'
import { useAuthStore } from '@/stores/auth'

const route = useRoute()
const authStore = useAuthStore()
const standalone = window.top === window.self
const submitting = ref(false)
const errorMessage = ref('')

const clientName = computed(() => {
  const clientId = route.query.client_id
  if (clientId === 'dataagent') return 'DataAgent'
  if (typeof clientId !== 'string') return ''
  try {
    const payload = clientId.split('.')[1].replace(/-/g, '+').replace(/_/g, '/')
    const bytes = Uint8Array.from(atob(payload), (character) => character.charCodeAt(0))
    const { client_name: name } = JSON.parse(new TextDecoder().decode(bytes))
    return typeof name === 'string' ? name : ''
  } catch {
    return ''
  }
})

const callbackHost = computed(() => {
  if (typeof route.query.redirect_uri !== 'string') return ''
  try {
    const callback = new URL(route.query.redirect_uri)
    return callback.host || callback.protocol
  } catch {
    return ''
  }
})

async function decide(decision) {
  if (submitting.value) return
  submitting.value = true
  errorMessage.value = ''
  try {
    const { redirect_url: redirectUrl } = await dataagentApi.decideMcpAuthorization({ ...route.query, decision })
    window.location.assign(redirectUrl)
  } catch (error) {
    errorMessage.value = error?.response?.status === 503
      ? '授权服务暂时不可用，请稍后重试。'
      : '授权未完成，请从 MCP 客户端重新发起。'
    submitting.value = false
  }
}
</script>

<style scoped>
.consent-page {
  min-height: 100vh;
  display: grid;
  place-items: center;
  padding: 24px;
  background: var(--el-fill-color-light);
}
.consent-card { width: min(100%, 440px); }
dl {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  gap: 8px 16px;
}
dt { font-weight: 600; }
dd { margin: 0; overflow-wrap: anywhere; }
.consent-actions { display: flex; justify-content: flex-end; }
</style>
