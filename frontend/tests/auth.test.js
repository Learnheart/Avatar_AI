/**
 * Unit tests for the sign-in: auth/session.js (the session of the page) and
 * auth/format.js (pure helpers).
 * Run: npm test
 */

import { test } from 'node:test'
import assert from 'node:assert/strict'

import { NETWORK_FAILURE, SUPERSEDED } from '../src/memory/format.js'
import {
  authErrorMessage,
  authHeaders,
  base64url,
  challengeOf,
  codeExpiredMessage,
  codeRefusal,
  codeSentMessage,
  emailRefusal,
  forgetDelayMs,
  identityLabel,
  linkErrorMessage,
  linkableProviders,
  linkedMessage,
  modeLabel,
  passwordRefusal,
  providerErrorMessage,
  providerLabel,
  randomToken,
  readFragment,
  registerErrorMessage,
  sessionErrorMessage,
  unlinkErrorMessage,
  verifyErrorMessage,
} from '../src/auth/format.js'
import {
  PageSession,
  SESSION_VERDICT,
  keepVerifier,
  sessionVerdict,
  takeVerifier,
} from '../src/auth/session.js'

const LINH = { id: 'u1', display_name: 'Khánh Linh' }
const AN = { id: 'u2', display_name: 'An' }

function failure(status) {
  const error = new Error(`status ${status}`)
  error.status = status
  return error
}

/** A request that answers when the test says so. */
function pending() {
  const call = {}
  call.promise = new Promise((resolve, reject) => {
    call.resolve = resolve
    call.reject = reject
  })
  return call
}

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

  session.confirm('t1', LINH)
  session.confirm('t1', { id: 'u1', display_name: 'Linh' })

  assert.equal(session.token, 't1')
  assert.equal(session.user.display_name, 'Linh')
  assert.deepEqual(seen, ['u1'])
})

test('the backend saying nobody is signed in makes the page a guest', () => {
  const storage = new FakeStorage()
  const session = new PageSession(storage)
  session.signIn({ token: 't1', user: LINH })

  session.confirm('t1', null)

  assert.equal(session.token, null)
  assert.deepEqual(storage.items, {})
})

test('an answer about another token changes nothing', () => {
  const session = new PageSession(new FakeStorage())
  session.signIn({ token: 't2', user: AN })
  const seen = watched(session)

  session.confirm('t1', null)   // the late answer for the person before
  session.confirm('t1', LINH)
  session.confirm(null, null)

  assert.equal(session.token, 't2')
  assert.deepEqual(session.user, AN)
  assert.deepEqual(seen, [])
})

test('generation grows on sign-in, sign-out and a change of user, not on a confirmation', () => {
  const session = new PageSession(new FakeStorage())
  const start = session.generation

  session.signIn({ token: 't1', user: LINH })
  assert.equal(session.generation, start + 1)

  session.signIn({ token: 't1', user: LINH })
  session.confirm('t1', LINH)
  session.confirm('t1', { id: 'u1', display_name: 'Linh' })   // the same person, renamed
  assert.equal(session.generation, start + 1)

  session.signIn({ token: 't2', user: AN })                    // another person
  assert.equal(session.generation, start + 2)

  session.confirm('t2', { id: 'u9', display_name: 'An' })      // the token is someone else's
  assert.equal(session.generation, start + 3)

  session.signOut()
  assert.equal(session.generation, start + 4)
  session.signOut()                                            // a guest signing out
  assert.equal(session.generation, start + 4)
})

test('rejected(token) signs out for the current token, does nothing for another or none', () => {
  const session = new PageSession(new FakeStorage())
  session.signIn({ token: 't2', user: AN })
  const seen = watched(session)

  assert.equal(session.rejected('t1'), false)   // the token of the person before
  assert.equal(session.rejected(null), false)
  assert.equal(session.rejected(undefined), false)
  assert.equal(session.token, 't2')
  assert.deepEqual(seen, [])

  assert.equal(session.rejected('t2'), true)
  assert.equal(session.token, null)
  assert.equal(session.user, null)
  assert.deepEqual(seen, [null])

  assert.equal(session.rejected(null), false)   // a guest has no token to refuse
  assert.deepEqual(seen, [null])
})

// ── A request made for whoever is on the page ───────────────────────────────

test('a request is given the token of the page and its answer', async () => {
  const session = new PageSession(new FakeStorage())
  session.signIn({ token: 't1', user: LINH })

  assert.equal(await session.whileCurrent(async (token) => `asked with ${token}`), 'asked with t1')
  assert.equal(await new PageSession(null).whileCurrent(async (token) => token), null)
})

test('an answer that comes after the person changed is superseded', async () => {
  const session = new PageSession(new FakeStorage())
  session.signIn({ token: 't1', user: LINH })

  const answered = pending()
  const asked = session.whileCurrent(() => answered.promise)
  session.signOut()
  session.signIn({ token: 't2', user: AN })
  answered.resolve({ items: ['of Linh'] })

  await assert.rejects(asked, (err) => err.status === SUPERSEDED)
})

test('a late 401 for the token before does not sign out who is there now', async () => {
  const session = new PageSession(new FakeStorage())
  session.signIn({ token: 't1', user: LINH })

  const answered = pending()
  const asked = session.whileCurrent(() => answered.promise, { endsOn401: true })
  session.signIn({ token: 't2', user: AN })
  answered.reject(failure(401))

  await assert.rejects(asked, (err) => err.status === SUPERSEDED)
  assert.equal(session.token, 't2')
  assert.deepEqual(session.user, AN)
})

test('a 401 for the token the page still holds makes it a guest, and is told as a 401', async () => {
  const session = new PageSession(new FakeStorage())
  session.signIn({ token: 't1', user: LINH })

  await assert.rejects(
    session.whileCurrent(async () => { throw failure(401) }, { endsOn401: true }),
    (err) => err.status === 401,
  )
  assert.equal(session.token, null)
})

test('a 401 signs nobody out on a route where it is not about the session', async () => {
  const session = new PageSession(new FakeStorage())
  session.signIn({ token: 't1', user: LINH })

  await assert.rejects(
    session.whileCurrent(async () => { throw failure(401) }),
    (err) => err.status === 401,
  )
  await assert.rejects(
    session.whileCurrent(async () => { throw failure(502) }, { endsOn401: true }),
    (err) => err.status === 502,
  )
  assert.equal(session.token, 't1')
})

test('a confirmation of the same user supersedes nothing', async () => {
  const session = new PageSession(new FakeStorage())
  session.signIn({ token: 't1', user: LINH })

  const answered = pending()
  const asked = session.whileCurrent(() => answered.promise)
  session.confirm('t1', { id: 'u1', display_name: 'Linh' })
  answered.resolve('kept')

  assert.equal(await asked, 'kept')
})

// ── The answer to an `auth` frame of the WebSocket ──────────────────────────

const SENT = { id: 'frame-2', token: 't1' }

function answer(status, overrides = {}) {
  return { type: 'session', user: null, reply_to: 'frame-2', status, ...overrides }
}

test('the token is dropped only for the last auth, carrying the current token, refused or ended', () => {
  assert.equal(sessionVerdict(answer('rejected'), SENT, 't1'), SESSION_VERDICT.DROP)
  assert.equal(sessionVerdict(answer('ended'), SENT, 't1'), SESSION_VERDICT.DROP)
})

test('an answer to an older auth frame is ignored', () => {
  for (const status of ['rejected', 'ended', 'guest', 'unresolved']) {
    const late = answer(status, { reply_to: 'frame-1' })
    assert.equal(sessionVerdict(late, SENT, 't1'), SESSION_VERDICT.IGNORE, status)
  }
  const other = answer('signed_in', { reply_to: 'frame-1', user: AN })
  assert.equal(sessionVerdict(other, SENT, 't1'), SESSION_VERDICT.IGNORE)
})

test('the late answer to the auth of page load does not undo a sign-in', () => {
  // The page sent auth(null), signed in, and its auth(t1) is not sent yet.
  const guestFrame = { id: 'frame-1', token: null }
  const late = { type: 'session', user: null, reply_to: 'frame-1', status: 'guest' }

  assert.equal(sessionVerdict(late, guestFrame, 't1'), SESSION_VERDICT.IGNORE)
})

test('an answer to a frame that carried another token is ignored', () => {
  // The last frame carried the token of the person before; the page holds t2 now.
  assert.equal(sessionVerdict(answer('rejected'), SENT, 't2'), SESSION_VERDICT.IGNORE)
  assert.equal(sessionVerdict(answer('ended'), SENT, 't2'), SESSION_VERDICT.IGNORE)
  assert.equal(
    sessionVerdict(answer('signed_in', { user: LINH }), SENT, 't2'), SESSION_VERDICT.IGNORE,
  )
})

test('an answer without reply_to, or with nothing sent, is ignored', () => {
  assert.equal(sessionVerdict(answer('rejected', { reply_to: null }), SENT, 't1'), SESSION_VERDICT.IGNORE)
  assert.equal(sessionVerdict({ type: 'session', user: null }, SENT, 't1'), SESSION_VERDICT.IGNORE)
  assert.equal(sessionVerdict(answer('rejected'), null, 't1'), SESSION_VERDICT.IGNORE)
  assert.equal(sessionVerdict(undefined, SENT, 't1'), SESSION_VERDICT.IGNORE)
})

test('unresolved keeps the token and asks to present it again', () => {
  assert.equal(sessionVerdict(answer('unresolved'), SENT, 't1'), SESSION_VERDICT.RETRY)
})

test('the user is taken only from the answer to the last auth of the current token', () => {
  assert.equal(
    sessionVerdict(answer('signed_in', { user: LINH }), SENT, 't1'), SESSION_VERDICT.ACCEPT,
  )
  assert.equal(sessionVerdict(answer('signed_in'), SENT, 't1'), SESSION_VERDICT.IGNORE)
})

test('a guest has nothing to drop or to take', () => {
  const guestFrame = { id: 'frame-2', token: null }

  for (const status of ['guest', 'rejected', 'ended', 'unresolved']) {
    assert.equal(sessionVerdict(answer(status), guestFrame, null), SESSION_VERDICT.IGNORE, status)
  }
  // AUTH_DEV_USER_ID on the backend: a user without a token is not a session of the page.
  assert.equal(
    sessionVerdict(answer('signed_in', { user: LINH }), guestFrame, null), SESSION_VERDICT.IGNORE,
  )
})

test('an unknown status changes nothing', () => {
  assert.equal(sessionVerdict(answer('guest'), SENT, 't1'), SESSION_VERDICT.IGNORE)
  assert.equal(sessionVerdict(answer('something-new'), SENT, 't1'), SESSION_VERDICT.IGNORE)
})

// ── The verifier of a sign-in at a provider ─────────────────────────────────

test('base64url has no padding and no character a URL would change', () => {
  assert.equal(base64url(new Uint8Array([])), '')
  assert.equal(base64url(new Uint8Array([0xfb, 0xff, 0xfe])), '-__-')
  assert.equal(base64url(new Uint8Array([0x66])), 'Zg')
  assert.equal(base64url(new Uint8Array([0x66, 0x6f]).buffer), 'Zm8')
})

test('the challenge is the base64url SHA-256 of the verifier', async () => {
  // The example of RFC 7636, appendix B.
  assert.equal(
    await challengeOf('dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk'),
    'E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM',
  )
  await assert.rejects(challengeOf('abc', null), /SHA-256/)
})

test('each start has a fresh verifier of at least 32 random bytes', () => {
  const first = randomToken(32)
  const second = randomToken(32)

  assert.notEqual(first, second)
  assert.match(first, /^[A-Za-z0-9_-]{43}$/)   // 32 bytes, base64url, no padding

  const asked = []
  const fixed = { getRandomValues(bytes) { asked.push(bytes.length); return bytes.fill(255) } }
  assert.equal(randomToken(3, fixed), '____')
  assert.deepEqual(asked, [3])
})

test('the verifier is kept for one sign-in: taking it removes it', () => {
  const storage = new FakeStorage()

  assert.equal(takeVerifier(storage), null)   // this page started no sign-in
  assert.equal(keepVerifier(storage, 'v1'), true)
  assert.equal(takeVerifier(storage), 'v1')
  assert.equal(takeVerifier(storage), null)
  assert.deepEqual(storage.items, {})
})

test('a verifier that cannot be kept is said so, and none is found', () => {
  const broken = {
    getItem() { throw new Error('denied') },
    setItem() { throw new Error('full') },
    removeItem() { throw new Error('denied') },
  }

  assert.equal(keepVerifier(broken, 'v1'), false)
  assert.equal(keepVerifier(null, 'v1'), false)
  assert.equal(takeVerifier(broken), null)
  assert.equal(takeVerifier(null), null)
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

test('emailRefusal accepts what the backend accepts', () => {
  for (const email of ['linh@example.com', ' Linh.Nguyen@Example.COM ', 'a+b@x.vn']) {
    assert.equal(emailRefusal(email), null, email)
  }
  for (const email of ['', 'linh', 'linh@', '@example.com', 'linh@example', 'linh nguyen@example.com',
    'a@b@example.com', `${'x'.repeat(250)}@example.com`, null]) {
    assert.match(emailRefusal(email), /email hợp lệ/, String(email))
  }
})

test('codeRefusal wants six digits and ignores the spaces typed in them', () => {
  assert.equal(codeRefusal('123456'), null)
  assert.equal(codeRefusal(' 123 456 '), null)
  for (const code of ['', '12345', '1234567', '12a456', null]) {
    assert.match(codeRefusal(code), /6 chữ số/, String(code))
  }
})

test('the messages of the two steps of a new account', () => {
  assert.match(registerErrorMessage(409), /đã có tài khoản/)
  assert.match(registerErrorMessage(429), /vừa được gửi/)
  assert.match(registerErrorMessage(502), /Chưa gửi được mã/)
  assert.equal(registerErrorMessage(403), authErrorMessage(403))
  assert.match(verifyErrorMessage(401), /Mã không đúng/)
  assert.match(verifyErrorMessage(410), /yêu cầu mã mới/)
  assert.equal(verifyErrorMessage(502), authErrorMessage(502))
  assert.equal(
    codeSentMessage({ email: 'linh@example.com', expires_in_sec: 600 }),
    'Đã gửi mã gồm 6 chữ số tới linh@example.com. Mã có hiệu lực trong 10 phút.',
  )
  assert.match(codeSentMessage({ email: 'a@b.c', expires_in_sec: 20 }), /trong 1 phút/)
})

test('the delay before the form forgets follows expires_in_sec', () => {
  assert.equal(forgetDelayMs({ email: 'a@b.c', expires_in_sec: 600 }), 600000)
  assert.equal(forgetDelayMs({ expires_in_sec: 1.5 }), 1500)
  // Without a duration nothing is kept waiting.
  for (const sent of [{}, { expires_in_sec: 0 }, { expires_in_sec: -5 }, { expires_in_sec: 'soon' }, null]) {
    assert.equal(forgetDelayMs(sent), 0)
  }
  assert.match(codeExpiredMessage(), /hết hạn/)
})

test('401 on a session route reads as an ended session; on sign-in as wrong credentials', () => {
  assert.match(sessionErrorMessage(401), /Phiên đăng nhập đã hết/)
  assert.match(sessionErrorMessage(401, unlinkErrorMessage), /Phiên đăng nhập đã hết/)
  assert.match(sessionErrorMessage(401, linkErrorMessage), /Phiên đăng nhập đã hết/)
  assert.match(authErrorMessage(401), /Sai email hoặc mật khẩu/)
  // Everything else reads as the route says.
  assert.equal(sessionErrorMessage(502), authErrorMessage(502))
  assert.equal(sessionErrorMessage(409, unlinkErrorMessage), unlinkErrorMessage(409))
  assert.equal(sessionErrorMessage(NETWORK_FAILURE), authErrorMessage(NETWORK_FAILURE))
})

test('linkErrorMessage names each refusal of a link confirmation', () => {
  assert.match(linkErrorMessage(403), /tài khoản khác/)
  // One message for both causes of a 409: it names neither "another user" nor "you".
  assert.equal(linkErrorMessage(409), 'Không liên kết được: tài khoản này đã được liên kết')
  assert.match(linkErrorMessage(410), /hết hạn/)
  assert.equal(linkErrorMessage(502), authErrorMessage(502))
  assert.notEqual(linkErrorMessage(403), authErrorMessage(403))   // not "no accounts here"
  assert.notEqual(linkErrorMessage(409), authErrorMessage(409))   // not "email has an account"
})

test('passwordRefusal follows the minimum length', () => {
  assert.equal(passwordRefusal('12345678', 8), null)
  assert.match(passwordRefusal('1234567', 8), /ít nhất 8 ký tự/)
  assert.match(passwordRefusal(undefined, 8), /ít nhất 8/)
})

test('authErrorMessage names each refusal', () => {
  assert.equal(authErrorMessage(NETWORK_FAILURE), 'Không kết nối được máy chủ')
  assert.match(authErrorMessage(401), /Sai email hoặc mật khẩu/)
  assert.match(authErrorMessage(403), /không cho phép tạo tài khoản/)
  assert.match(authErrorMessage(409), /đã có tài khoản/)
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
  assert.deepEqual(readFragment('#auth_link=ticket-1'), { link: 'ticket-1' })
  assert.equal(readFragment('#auth_linked=google'), null)   // the callback attaches nothing itself
  assert.deepEqual(readFragment('auth_error=denied'), { error: 'denied' })
  assert.equal(readFragment('#auth_code='), null)
  assert.equal(readFragment('#section-2'), null)
  assert.equal(readFragment(''), null)
  assert.equal(readFragment(undefined), null)
})

test('providerErrorMessage names each reason and has a fallback', () => {
  assert.match(providerErrorMessage('denied'), /huỷ/)
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
