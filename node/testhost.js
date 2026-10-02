'use strict';
// A host service that already speaks x402, written as if Nano did not exist.
// The mirror image of testhost.py, entry for entry, so the conformance suite
// compares like with like.

const USDC_ENTRIES = [
  { scheme: 'exact', network: 'base', asset: 'USDC', maxAmountRequired: '0.01',
    payTo: '0x1111111111111111111111111111111111111111',
    resource: 'https://example.dev/report', description: 'USDC on Base' },
  { scheme: 'exact', network: 'solana', asset: 'USDC', maxAmountRequired: '0.01',
    payTo: 'So11111111111111111111111111111111111111112',
    resource: 'https://example.dev/report', description: 'USDC on Solana' },
];

const X402_VERSION = '2';
const BODY = '{"report": "the goods"}';

function challenge(entries) {
  return { x402Version: 2, error: 'payment required',
           accepts: (entries || USDC_ENTRIES).map((e) => ({ ...e })) };
}

function makeHost(entries, body = BODY) {
  const protectedHandler = (req, res) => {
    res.writeHead(200, { 'Content-Type': 'application/json', 'X-402-Version': X402_VERSION });
    res.end(body);
  };

  const gate = (req, res, next) => {
    const header = req.headers['x-payment'];
    if (header) {
      let payload = {};
      try { payload = JSON.parse(header); } catch { payload = {}; }
      if (payload.network === 'base' || payload.network === 'solana') {
        return protectedHandler(req, res);
      }
    }
    const blob = JSON.stringify(challenge(entries));
    res.writeHead(402, {
      'Content-Type': 'application/json',
      'X-402-Version': X402_VERSION,
      'Cache-Control': 'no-store',
      'Content-Length': String(Buffer.byteLength(blob)),
    });
    return res.end(blob);
  };

  return { gate, protected: protectedHandler };
}

class FakeNode {
  constructor() { this.blocks = new Map(); this.calls = []; }

  settle(blockHash, destination, amountRaw, confirmed = true) {
    this.blocks.set(blockHash.toUpperCase(), {
      confirmed, destination, amount_raw: BigInt(amountRaw), subtype: 'send' });
    return blockHash.toUpperCase();
  }

  unconfirmed(blockHash, destination, amountRaw) {
    return this.settle(blockHash, destination, amountRaw, false);
  }

  async blockInfo(blockHash) {
    this.calls.push(blockHash.toUpperCase());
    const found = this.blocks.get(blockHash.toUpperCase());
    return found ? { ...found } : {};
  }
}

class DeadNode {
  constructor() { this.calls = []; }

  async blockInfo(blockHash) {
    const { NodeError } = require('./node-client.js');
    this.calls.push(blockHash);
    throw new NodeError('node_unreachable',
      'could not reach the Nano node at node.example: timed out');
  }
}

module.exports = { USDC_ENTRIES, X402_VERSION, BODY, challenge, makeHost, FakeNode, DeadNode };
