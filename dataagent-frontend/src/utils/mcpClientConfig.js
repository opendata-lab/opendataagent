export function stringifyMcpClientConfig(name, path, token = '<你的访问令牌>') {
  return JSON.stringify({
    mcpServers: {
      [`dataagent-${name}`]: {
        type: 'http',
        url: `${window.location.origin}${path}`,
        headers: { Authorization: `Bearer ${token}` }
      }
    }
  }, null, 2)
}
