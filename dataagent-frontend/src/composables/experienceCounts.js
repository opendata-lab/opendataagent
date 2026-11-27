import { ref } from 'vue'
import { dataagentApi } from '@/api/dataagent'

export const pendingKnowledgeCount = ref(0)
let sequence = 0

export async function refreshPendingKnowledgeCount() {
  const current = ++sequence
  try {
    const result = await dataagentApi.listExperiences({ status: 'pending', page_size: 1 })
    if (current === sequence) pendingKnowledgeCount.value = Number(result.total || 0)
  } catch {
    if (current === sequence) pendingKnowledgeCount.value = 0
  }
}

export function clearPendingKnowledgeCount() {
  sequence++
  pendingKnowledgeCount.value = 0
}
