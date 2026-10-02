'use strict';
// dualRail() - Express/Connect middleware that appends XNO to accepts[] and
// verifies Nano payments, leaving every existing rail untouched.
//
// Read this first, because it is the objection an operator will have: IF OUR
// RAIL FAILS, YOURS KEEPS SELLING. Every error on the Nano side - unreachable
// node, timeout, malformed proof, unknown block - falls through to your
// existing rails exactly as if this middleware were not installed. There is no
// path here that turns a Nano problem into a 5xx for a customer paying in USDC.
//
//   app.use(dualRail({ payTo: 'nano_...', amountXno: '0.0001',
//                      nodeUrl: process.env.NANO_NODE_URL, gate: x402Middleware }));
//
// Without `gate` it does the additive half only: a 402 written by anything
// downstream gets one entry appended on its way out. With `gate` - the
// middleware that IS your paywall - a verified Nano payment bypasses it, which
// is the only thing "admit the request" can mean.

const challenge = require('./challenge.js');
const money = require('./money.js');
const address = require('./address.js');
const { NodeError, HttpNanoNode, DEFAULT_TIMEOUT_MS } = require('./node-client.js');

const PAYMENT_HEADER = 'x-payment';
const ONLY_PERMITTED_ON_VERIFY_ERROR = 'fallthrough';

class ConfigurationError extends Error {
  constructor(reason, message) {
    super(message);
    this.reason = reason;
    this.name = 'ConfigurationError';
  }
}

class Unpaid extends Error {
  constructor(reason, message, detail = {}) {
    super(message);
    this.reason = reason;
    this.detail = detail;
    this.name = 'Unpaid';
  }
}

class ReplayGuard {
  constructor() { this.seenByResource = new Map(); }

  seen(resource, blockHash) {
    return (this.seenByResource.get(resource || '') || new Set()).has(blockHash);
  }

  remember(resource, blockHash) {
    const key = resource || '';
    if (!this.seenByResource.has(key)) this.seenByResource.set(key, new Set());
    this.seenByResource.get(key).add(blockHash);
  }
}

function parsePayment(header) {
  if (!header || !String(header).trim()) throw new Unpaid('no_payment', 'no X-PAYMENT header');
  let text = String(header).trim();
  if (!text.startsWith('{')) {
    try {
      text = Buffer.from(text, 'base64').toString('utf8');
      if (!text.trim().startsWith('{')) throw new Error('not json');
    } catch {
      throw new Unpaid('malformed_payment', 'X-PAYMENT is neither JSON nor base64 of JSON');
    }
  }
  let payload;
  try {
    payload = JSON.parse(text);
  } catch {
    throw new Unpaid('malformed_payment', 'X-PAYMENT is not valid JSON');
  }
  if (!payload || typeof payload !== 'object' || Array.isArray(payload)) {
    throw new Unpaid('malformed_payment', 'X-PAYMENT is not an object');
  }
  return payload;
}

function blockHashOf(payload) {
  const inner = payload.payload && typeof payload.payload === 'object' ? payload.payload : payload;
  for (const key of ['blockHash', 'block_hash', 'hash']) {
    const value = inner[key];
    if (typeof value === 'string' && value.trim()) {
      const candidate = value.trim().toUpperCase();
      if (/^[0-9A-F]{64}$/.test(candidate)) return candidate;
      throw new Unpaid('malformed_payment',
        `block hash must be 64 hex characters, got ${candidate.length}`);
    }
  }
  throw new Unpaid('malformed_payment', 'X-PAYMENT carries no block hash');
}

function isOurs(payload) {
  const network = payload.network
    ?? (payload.payload && typeof payload.payload === 'object' ? payload.payload.network : undefined);
  return network === challenge.NETWORK;
}

class DualRail {
  constructor(options = {}) {
    const { payTo, amountXno, nodeUrl, network = challenge.NETWORK,
            onVerifyError = ONLY_PERMITTED_ON_VERIFY_ERROR, node, gate,
            timeoutMs = DEFAULT_TIMEOUT_MS, logger } = options;

    const verdict = address.validate(payTo);
    if (!verdict.valid) {
      throw new ConfigurationError('invalid_address',
        `payTo ${JSON.stringify(payTo)} fails its checksum (${verdict.reason}). ` +
        'A service must not boot advertising an address that can never receive a payment.');
    }
    try {
      this.amountRaw = money.checkPrice(amountXno);
    } catch (err) {
      throw new ConfigurationError('price_out_of_range', err.message);
    }
    if (onVerifyError !== ONLY_PERMITTED_ON_VERIFY_ERROR) {
      throw new ConfigurationError('invalid_on_verify_error',
        `onVerifyError must be ${JSON.stringify(ONLY_PERMITTED_ON_VERIFY_ERROR)} and ` +
        'nothing else. Failing closed on our rail would let our outage cost you a sale on yours.');
    }
    if (network !== challenge.NETWORK) {
      throw new ConfigurationError('invalid_network',
        `network is fixed at ${JSON.stringify(challenge.NETWORK)}`);
    }

    this.payTo = verdict.normalised;
    this.amountXno = String(amountXno);
    this.node = node || (nodeUrl ? new HttpNanoNode(nodeUrl, timeoutMs) : null);
    this.gate = gate || null;
    this.guard = new ReplayGuard();
    this.logger = logger || null;
    this.warned = new Set();
  }

  patchChallenge(body) {
    return challenge.appendNano(body, this.payTo, this.amountXno);
  }

  async checkPayment(header, resource) {
    if (!this.node) return null;
    try {
      return await this.verify(header, resource);
    } catch (err) {
      this.warnOnce(err instanceof Unpaid ? err
        : new Unpaid('adapter_error', err.message, { fallthrough: true }));
      return null;
    }
  }

  async verify(header, resource) {
    const payload = parsePayment(header);
    if (!isOurs(payload)) {
      throw new Unpaid('not_our_rail',
        `this proof is for ${JSON.stringify(payload.network)}, not ` +
        `${JSON.stringify(challenge.NETWORK)}`);
    }
    const blockHash = blockHashOf(payload);
    if (this.guard.seen(resource, blockHash)) {
      throw new Unpaid('replayed',
        `block ${blockHash.slice(0, 12)} has already been spent on this resource`);
    }

    let info;
    try {
      info = await this.node.blockInfo(blockHash);
    } catch (err) {
      if (err instanceof NodeError) {
        throw new Unpaid('node_unavailable', err.message, { fallthrough: true });
      }
      throw err;
    }

    if (!info || Object.keys(info).length === 0) {
      throw new Unpaid('unknown_block', `block ${blockHash.slice(0, 12)} is not known to the node`);
    }
    if (!info.confirmed) {
      throw new Unpaid('unconfirmed', `block ${blockHash.slice(0, 12)} is not confirmed yet`);
    }
    if (info.destination !== this.payTo) {
      throw new Unpaid('wrong_destination',
        `block ${blockHash.slice(0, 12)} paid ${info.destination}, not ${this.payTo}`);
    }
    const paidRaw = BigInt(info.amount_raw ?? 0n);
    if (paidRaw < this.amountRaw) {
      throw new Unpaid('underpaid',
        `block ${blockHash.slice(0, 12)} paid ${money.rawToXno(paidRaw)} XNO, ` +
        `${money.rawToXno(this.amountRaw)} XNO is required`,
        { shortfallRaw: this.amountRaw - paidRaw });
    }
    this.guard.remember(resource, blockHash);
    return { blockHash, amountRaw: paidRaw, payTo: this.payTo,
             overpaidRaw: paidRaw - this.amountRaw };
  }

  warnOnce(err) {
    if (err.reason === 'no_payment' || err.reason === 'not_our_rail') return;
    if (err.detail && err.detail.fallthrough) {
      if (this.warned.has(err.reason)) return;
      this.warned.add(err.reason);
    }
    if (this.logger) {
      this.logger(`dual-rail: ${err.message} (${err.reason}) - falling through to ` +
                  'the existing rails');
    }
  }
}

function resourceOfRequest(req) {
  const host = req.headers.host || '';
  const proto = req.headers['x-forwarded-proto'] || (req.socket && req.socket.encrypted ? 'https' : 'http');
  return `${proto}://${host}${(req.originalUrl || req.url || '').split('?')[0]}`;
}

function dualRail(options = {}) {
  const rail = new DualRail(options);

  const middleware = async function dualRailMiddleware(req, res, next) {
    const resource = resourceOfRequest(req);
    const payment = await rail.checkPayment(req.headers[PAYMENT_HEADER], resource);
    if (payment && rail.gate) {
      req.dualRail = payment;
      return next();
    }
    patchOutgoing402(rail, res);
    if (rail.gate) return rail.gate(req, res, next);
    return next();
  };

  middleware.rail = rail;
  return middleware;
}

function patchOutgoing402(rail, res) {
  // Buffer the response, and DEFER THE HEADERS, only long enough to see whether
  // it is a 402 carrying an accepts[] array.
  //
  // The headers have to be deferred, not just the body: a host that writes its
  // own Content-Length has already committed it by the time we know the patched
  // body is longer, and setHeader after writeHead is ignored - which truncates
  // the JSON at exactly the host's original length. The Node tests caught that;
  // it is the reason writeHead is intercepted here and not only write and end.
  //
  // Anything that is not a 402 with an accepts[] array goes out exactly as it
  // was written, byte for byte.
  const originalWriteHead = res.writeHead.bind(res);
  const originalWrite = res.write.bind(res);
  const originalEnd = res.end.bind(res);
  const chunks = [];
  let head = null;

  res.writeHead = function writeHead(statusCode, ...rest) {
    head = [statusCode, ...rest];
    res.statusCode = statusCode;
    return res;
  };

  res.write = function write(chunk) {
    if (chunk) chunks.push(Buffer.from(chunk));
    return true;
  };

  res.end = function end(chunk) {
    if (chunk && typeof chunk !== 'function') chunks.push(Buffer.from(chunk));
    let body = Buffer.concat(chunks);

    if (res.statusCode === 402) {
      try {
        const parsed = JSON.parse(body.toString('utf8'));
        const patched = rail.patchChallenge(parsed);
        if (JSON.stringify(patched) !== JSON.stringify(parsed)) {
          body = Buffer.from(JSON.stringify(patched), 'utf8');
        }
      } catch {
        // A 402 we do not understand is a 402 we must not rewrite.
      }
    }

    res.writeHead = originalWriteHead;
    res.write = originalWrite;
    res.end = originalEnd;

    if (head) {
      const headers = head.find((part, index) => index > 0 && part && typeof part === 'object');
      if (headers) {
        for (const key of Object.keys(headers)) {
          if (key.toLowerCase() === 'content-length') headers[key] = String(body.length);
        }
      }
      originalWriteHead(...head);
    } else if (res.getHeader('Content-Length') !== undefined) {
      res.setHeader('Content-Length', String(body.length));
    }

    if (body.length) originalWrite(body);
    return originalEnd();
  };
}

module.exports = { dualRail, DualRail, ConfigurationError, Unpaid, ReplayGuard,
                   parsePayment, blockHashOf, isOurs, PAYMENT_HEADER };
