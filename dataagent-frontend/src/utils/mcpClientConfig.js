export function mcpClientEndpoint(path) {
  return `${window.location.origin}${path}`
}

export function stringifyMcpClientConfig(name, path, token = '<你的访问令牌>') {
  return JSON.stringify({
    mcpServers: {
      [`dataagent-${name}`]: {
        type: 'http',
        url: mcpClientEndpoint(path),
        headers: { Authorization: `Bearer ${token}` }
      }
    }
  }, null, 2)
}
