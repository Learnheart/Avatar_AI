/**
 * Unit tests for the sign-in: auth/session.js (the session of the page) and
 * auth/format.js (pure helpers).
 * Run: npm test
 */

import { test } from 'node:test'
import assert from 'node:assert/strict'

import { NETWORK_FAILURE } from '../src/memory/format.js'
import {
  authErrorMessage,
  authHeaders,
  identityLabel,
  linkableProviders,
  linkedMessage,
  modeLabel,
  passwordRefusal,
  providerErrorMessage,
  providerLabel,
  readFragment,
  unlinkErrorMessage,
  usernameRefusal,
} from '../src/auth/format.js'
import { PageSession } from '../src/auth/session.js'

const LINH = { id: 'u1', display_name: 'Khánh Linh' }

class FakeStorage {
  constructor(items = {}) { this.items = { ...items } }
  getItem(key) { return this.items[key] ?? null }
  setItem(key, value) { this.items[key] = String(value) }
  removeItem(key) { delete this.items[key] }
}

function watched(session) {
  const seen = []
  session.onChange((changed) => seen.push(changed.user?.id ?? null))
  return seen
}

// ── The session of the page ─────────────────────────────────────────────────

test('a page starts as a guest', () => {
  const session = new PageSession(new FakeStorage())

  assert.equal(session.token, null)
  assert.equal(session.user, null)
})

test('signing in holds the token and the user, and tells the listeners once', () => {
  const session = new PageSession(new FakeStorage())
  const seen = watched(session)

  session.signIn({ token: 't1', user: LINH, expires_at: 'later' })
  session.signIn({ token: 't1', user: LINH })

  assert.equal(session.token, 't1')
  assert.deepEqual(session.user, LINH)
  assert.deepEqual(seen, ['u1'])
})

test('signing out drops both and tells the listeners; a guest signing out tells nobody', () => {
  const session = new PageSession(new FakeStorage())
  session.signIn({ token: 't1', user: LINH })
  const seen = watched(session)

  session.signOut()
  session.signOut()

  assert.equal(session.token, null)
  assert.equal(session.user, null)
  assert.deepEqual(seen, [null])
})

test('the session is kept across a restart of the page and dropped with a sign-out', () => {
  const storage = new FakeStorage()
  new PageSession(storage).signIn({ token: 't1', user: LINH })

  const restarted = new PageSession(storage)
  assert.equal(restarted.token, 't1')
  assert.deepEqual(restarted.user, LINH)

  restarted.signOut()
  assert.equal(new PageSession(storage).token, null)
  assert.deepEqual(storage.items, {})
})

test('the backend confirming the user keeps the session, with the name it gave', () => {
  const session = new PageSession(new FakeStorage())
  session.signIn({ token: 't1', user: LINH })
  const seen = watched(session)

  session.confirm(LINH)
  session.confirm({ id: 'u1', display_name: 'Linh' })

  assert.equal(session.token, 't1')
  assert.equal(session.user.display_name, 'Linh')
  assert.deepEqual(seen, ['u1'])
})

test('the backend saying nobody is signed in makes the page a guest', () => {
  const storage = new FakeStorage()
  const session = new PageSession(storage)
  session.signIn({ token: 't1', user: LINH })

  session.confirm(null)

  assert.equal(session.token, null)
  assert.deepEqual(storage.items, {})
})

test('a session without a token or without a user is a guest', () => {
  const session = new PageSession(new FakeStorage())

  session.signIn({ token: '', user: LINH })
  assert.equal(session.user, null)
  session.signIn({ token: 't1', user: null })
  assert.equal(session.token, null)
})

test('a storage that holds something else, or fails, gives a guest and breaks nothing', () => {
  for (const kept of ['not json', '{"token": 7}', '{"token": "t1"}', 'null']) {
    assert.equal(new PageSession(new FakeStorage({ 'avatar.session': kept })).token, null)
  }
  const broken = {
    getItem() { throw new Error('denied') },
    setItem() { throw new Error('full') },
    removeItem() { throw new Error('denied') },
  }
  const session = new PageSession(broken)
  session.signIn({ token: 't1', user: LINH })
  assert.equal(session.token, 't1')   // signed in until the page closes
  session.signOut()
  assert.equal(session.token, null)
})

test('without a storage the session lives in memory', () => {
  const session = new PageSession(null)
  session.signIn({ token: 't1', user: LINH })

  assert.equal(session.token, 't1')
  assert.equal(new PageSession(null).token, null)
})

// ── Helpers ─────────────────────────────────────────────────────────────────

test('authHeaders presents a token and nothing for a guest', () => {
  assert.deepEqual(authHeaders('t1'), { Authorization: 'Bearer t1' })
  assert.deepEqual(authHeaders(null), {})
  assert.deepEqual(authHeaders(''), {})
})

test('usernameRefusal accepts what the backend accepts', () => {
  for (const name of ['linh', 'Linh.Nguyen', ' linh_01 ', 'a-b', 'x'.repeat(32)]) {
    assert.equal(usernameRefusal(name), null, name)
  }
  for (const name of ['', 'ab', 'x'.repeat(33), 'linh nguyen', 'linh@home', 'lính', null]) {
    assert.match(usernameRefusal(name), /3 đến 32/, String(name))
  }
})

test('passwordRefusal follows the minimum length', () => {
  assert.equal(passwordRefusal('12345678', 8), null)
  assert.match(passwordRefusal('1234567', 8), /ít nhất 8 ký tự/)
  assert.match(passwordRefusal(undefined, 8), /ít nhất 8/)
})

test('authErrorMessage names each refusal', () => {
  assert.equal(authErrorMessage(NETWORK_FAILURE), 'Không kết nối được máy chủ')
  assert.match(authErrorMessage(401), /Sai tên đăng nhập hoặc mật khẩu/)
  assert.match(authErrorMessage(403), /không cho phép tạo tài khoản/)
  assert.match(authErrorMessage(409), /đã có người dùng/)
  assert.match(authErrorMessage(422), /không hợp lệ/)
  assert.match(authErrorMessage(429), /quá nhiều lần/)
  assert.match(authErrorMessage(502), /thử lại/)
  assert.equal(authErrorMessage(500), 'Lỗi máy chủ (500)')
})

test('unlinkErrorMessage has its own 409', () => {
  assert.match(unlinkErrorMessage(409), /duy nhất/)
  assert.equal(unlinkErrorMessage(502), authErrorMessage(502))
})

test('readFragment reads what a provider callback left, and nothing else', () => {
  assert.deepEqual(readFragment('#auth_code=abc-123'), { code: 'abc-123' })
  assert.deepEqual(readFragment('#auth_linked=google'), { linked: 'google' })
  assert.deepEqual(readFragment('auth_error=denied'), { error: 'denied' })
  assert.equal(readFragment('#auth_code='), null)
  assert.equal(readFragment('#section-2'), null)
  assert.equal(readFragment(''), null)
  assert.equal(readFragment(undefined), null)
})

test('providerErrorMessage names each reason and has a fallback', () => {
  assert.match(providerErrorMessage('denied'), /huỷ/)
  assert.match(providerErrorMessage('linked_elsewhere'), /người dùng khác/)
  assert.match(providerErrorMessage('already_linked'), /tài khoản khác/)
  assert.match(providerErrorMessage('link_expired'), /hết hạn/)
  assert.match(providerErrorMessage('provider'), /không phản hồi/)
  assert.match(providerErrorMessage('state'), /không thành công/)
  assert.match(providerErrorMessage(undefined), /không thành công/)
})

test('providers and linked accounts are named for the page', () => {
  assert.equal(providerLabel('google'), 'Google')
  assert.equal(providerLabel('github'), 'github')
  assert.equal(linkedMessage('facebook'), 'Đã liên kết tài khoản Facebook')
  assert.equal(identityLabel({ provider: 'google', email: 'l@example.com' }), 'Google · l@example.com')
  assert.equal(identityLabel({ provider: 'facebook', email: null }), 'Facebook')
})

test('linkableProviders are the offered ones the user has not linked', () => {
  const offered = ['google', 'facebook']

  assert.deepEqual(linkableProviders(offered, []), offered)
  assert.deepEqual(linkableProviders(offered, [{ provider: 'google' }]), ['facebook'])
  assert.deepEqual(linkableProviders(offered, [{ provider: 'google' }, { provider: 'facebook' }]), [])
  assert.deepEqual(linkableProviders([], undefined), [])
})

test('modeLabel shows a guest or the name of the user', () => {
  assert.equal(modeLabel(null), 'Khách')
  assert.equal(modeLabel(LINH), 'Khánh Linh')
  assert.equal(modeLabel({ id: 'u1', display_name: '' }), 'Đã đăng nhập')
})
