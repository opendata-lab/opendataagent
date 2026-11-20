import { ref } from 'vue'
import { dataagentApi } from '@/api/dataagent'
export const pendingMemoryCount = ref(0)
let sequence = 0
export async function refreshMemoryCount() {
  const current = ++sequence
  try {
    const result = await dataagentApi.listExperiences({ kind: 'memory', status: 'pending', page_size: 1 })
    if (current === sequence) pendingMemoryCount.value = Number(result.total || 0)
  } catch {
    if (current === sequence) pendingMemoryCount.value = 0
  }
}
export function clearMemoryCount() { sequence++; pendingMemoryCount.value = 0 }
