'use strict';
// The only file here that reaches the network. One RPC call: block_info.

const DEFAULT_TIMEOUT_MS = 3000;

class NodeError extends Error {
  constructor(reason, message) {
    super(message);
    this.reason = reason;
    this.name = 'NodeError';
  }
}

function hostOf(url) {
  try {
    const rest = url.includes('://') ? url.split('://')[1] : url;
    const authority = rest.split('/')[0];
    return authority.split('@').pop() || 'the configured node';
  } catch {
    return 'the configured node';
  }
}

class HttpNanoNode {
  constructor(url, timeoutMs = DEFAULT_TIMEOUT_MS) {
    this.url = url;
    this.timeoutMs = timeoutMs;
  }

  async blockInfo(blockHash) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.timeoutMs);
    let answer;
    try {
      const response = await fetch(this.url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'User-Agent': 'dual-rail/1.0' },
        body: JSON.stringify({ action: 'block_info', json_block: 'true', hash: blockHash }),
        signal: controller.signal,
      });
      answer = await response.json();
    } catch (err) {
      throw new NodeError('node_unreachable',
        `could not reach the Nano node at ${hostOf(this.url)}: ${err.message}`);
    } finally {
      clearTimeout(timer);
    }
    if (answer.error) {
      if (answer.error === 'Block not found' || answer.error === 'Invalid block hash') return {};
      throw new NodeError('node_error',
        `the Nano node at ${hostOf(this.url)} returned: ${answer.error}`);
    }
    const contents = answer.contents || {};
    return {
      confirmed: String(answer.confirmed).toLowerCase() === 'true',
      destination: answer.subtype === 'send' ? contents.link_as_account : answer.block_account,
      amount_raw: answer.amount ? BigInt(answer.amount) : 0n,
      subtype: answer.subtype,
    };
  }
}

module.exports = { DEFAULT_TIMEOUT_MS, NodeError, HttpNanoNode, hostOf };
