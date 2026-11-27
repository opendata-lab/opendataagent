<template>
  <section v-if="item.source === 'digest'" class="entry-sources" aria-label="来源">
    <h3>来源</h3>
    <el-link v-if="item.digest_topic_id" type="primary" @click="openDigest">查看总结会话</el-link>
    <p v-else class="muted">没有总结会话记录</p>
    <ul>
      <li v-for="(src, index) in item.sources || []" :key="src.task_id">
        <span>{{ src.question || `来源 ${index + 1}` }}</span>
        <el-link v-if="src.topic_id" type="primary" @click="openSource(src)">打开会话</el-link>
        <p v-if="src.source === 'widget' && src.answer" class="source-answer">{{ src.answer }}</p>
      </li>
    </ul>
  </section>
</template>
<script setup>
import { useRouter } from 'vue-router'
import { conversationLocation } from '@/router/agentContext'
const props = defineProps({ item: { type: Object, required: true }, digestAgentId: { type: String, required: true } })
const router = useRouter()
function openDigest() { openSource({ agent_id: props.digestAgentId, topic_id: props.item.digest_topic_id }) }
function openSource(src) { router.push(conversationLocation(src)) }
</script>
<style scoped>
h3 { font-size: 14px; margin: 24px 0 12px; }
ul { list-style: none; padding: 0; margin: 8px 0; }
li { display: flex; flex-wrap: wrap; gap: 8px; justify-content: space-between; padding: 12px 0; border-bottom: 1px solid #eef2f8; overflow-wrap: anywhere; }
li > span:first-child { flex: 1; min-width: 0; }
.muted, .source-answer { color: #64748b; font-size: 13px; }
.source-answer { flex-basis: 100%; margin: 0; white-space: pre-wrap; }
</style>
