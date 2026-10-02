'use strict';
// Exact XNO <-> raw, in BigInt. Never a Number.
//
// 1 XNO = 10n**30n raw. A JavaScript Number is a double: 53 bits of mantissa
// against the ~100 a raw balance needs. `0.1 + 0.2` is the canonical example
// and it is not a joke here - it is somebody's money. Every amount below is a
// BigInt, and every decimal amount is a string that is parsed.

const RAW_PER_XNO = 10n ** 30n;
const DECIMALS = 30;
const MIN_PRICE_XNO = '0.000001';
const MAX_PRICE_XNO = '100';

class AmountError extends Error {
  constructor(reason, message) {
    super(message);
    this.reason = reason;
    this.name = 'AmountError';
  }
}

function xnoToRaw(amount) {
  if (typeof amount === 'number') {
    throw new AmountError(
      'float_amount',
      `an amount must be a decimal STRING, not a number: ${amount} cannot represent ` +
      'raw exactly and would round real money away');
  }
  const text = String(amount).trim();
  if (!text) throw new AmountError('invalid_amount', 'amount is empty');
  if (text.startsWith('-')) throw new AmountError('invalid_amount', 'amount must not be negative');
  if (!/^\d*(\.\d*)?$/.test(text) || text === '.') {
    throw new AmountError('invalid_amount', `amount is not a decimal number: ${amount}`);
  }
  const [whole, frac = ''] = text.split('.');
  if (frac.length > DECIMALS) {
    throw new AmountError('invalid_amount',
      `Nano has ${DECIMALS} decimal places; ${frac.length} were given`);
  }
  return BigInt(whole || '0') * RAW_PER_XNO + BigInt(frac.padEnd(DECIMALS, '0') || '0');
}

function rawToXno(raw) {
  if (typeof raw !== 'bigint') {
    throw new AmountError('invalid_raw', `raw must be a BigInt, got ${typeof raw}`);
  }
  if (raw < 0n) throw new AmountError('invalid_raw', 'raw must not be negative');
  const whole = raw / RAW_PER_XNO;
  const frac = (raw % RAW_PER_XNO).toString().padStart(DECIMALS, '0').replace(/0+$/, '');
  return frac ? `${whole}.${frac}` : `${whole}`;
}

function checkPrice(amount) {
  let raw;
  try {
    raw = xnoToRaw(amount);
  } catch (err) {
    throw new AmountError('price_out_of_range', err.message);
  }
  if (raw < xnoToRaw(MIN_PRICE_XNO) || raw > xnoToRaw(MAX_PRICE_XNO)) {
    throw new AmountError('price_out_of_range',
      `amountXno must be a decimal string between ${MIN_PRICE_XNO} and ${MAX_PRICE_XNO} ` +
      `XNO, got ${JSON.stringify(amount)}`);
  }
  return raw;
}

module.exports = { AmountError, RAW_PER_XNO, DECIMALS, MIN_PRICE_XNO, MAX_PRICE_XNO,
                   xnoToRaw, rawToXno, checkPrice };
