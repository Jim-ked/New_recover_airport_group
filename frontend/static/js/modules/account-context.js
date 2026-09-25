import { apiFetch } from './api-client.js';

let account = null;
let pending = null;

export function currentAccount() {
  return account;
}

export function clearAccount() {
  account = null;
  pending = null;
}

export function getAccount() {
  if (account) return Promise.resolve(account);
  if (pending) return pending;
  pending = apiFetch('/api/me')
    .then((value) => {
      account = value;
      globalThis.dispatchEvent(new CustomEvent('app:account-ready', { detail: value }));
      return value;
    })
    .catch((error) => {
      pending = null;
      throw error;
    });
  return pending;
}
