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

// What one `block_info` reply means, with no socket in the way. Separate from
// the HTTP call so the shapes a real node answers can be asserted against
// recorded replies rather than against a mock of our own assumptions - the
// assumption is what was wrong here.
function readBlockInfo(answer) {
  const contents = answer.contents || {};
  // A state block names its kind in `subtype`; a legacy block has no `subtype`
  // at all and names its kind in `contents.type` (measured against mainnet: a
  // 2019 send answers subtype=undefined, contents.type="send",
  // contents.destination set and contents.link_as_account absent). Either way
  // the kind is read off the block and never defaulted, so a block whose kind
  // we cannot read is not a send.
  const subtype = answer.subtype || contents.type || null;
  // The payee, from wherever this block spells it, and only for a send - never
  // `block_account`, which is the account the block BELONGS to: on a send that
  // is the payer, and on a receive it is us.
  const destination = subtype === 'send'
    ? (contents.link_as_account || contents.destination || null)
    : null;
  return {
    confirmed: String(answer.confirmed).toLowerCase() === 'true',
    destination,
    amount_raw: answer.amount ? BigInt(answer.amount) : 0n,
    subtype,
  };
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
    return readBlockInfo(answer);
  }
}

module.exports = { DEFAULT_TIMEOUT_MS, NodeError, HttpNanoNode, hostOf, readBlockInfo };
