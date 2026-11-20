export function entryForm(item) {
  return { title: item?.title || '', when_text: item?.when_text || '', body: item?.body || '',
    target: item?.scope === 'agent' ? item.agent_id : 'global', inject_mode: item?.inject_mode || 'always' }
}
export function entryPayload(form) {
  return { title: form.title.trim(), when_text: form.when_text.trim(), body: form.body.trim(),
    scope: form.target === 'global' ? 'global' : 'agent', agent_id: form.target === 'global' ? null : form.target,
    inject_mode: form.inject_mode }
}
