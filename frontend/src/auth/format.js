/**
 * Pure helpers of the sign-in (unit-tested, no DOM, no network).
 */

import { NETWORK_FAILURE } from '../memory/format.js'

/** The headers that present a token to the backend; none for a guest. */
export function authHeaders(token) {
  return token ? { Authorization: `Bearer ${token}` } : {}
}

const EMAIL = /^[^@\s]+@[^@\s]+\.[^@\s]+$/

/**
 * Why an email is refused before any request, or null when it can be sent: the backend
 * would refuse it anyway. The code sent to the address is what proves it, not this.
 */
export function emailRefusal(email) {
  const typed = String(email ?? '').trim()
  return typed.length <= 254 && EMAIL.test(typed) ? null : 'Vui lòng nhập một địa chỉ email hợp lệ'
}

/** Why a code is refused before any request, or null: six digits, spaces ignored. */
export function codeRefusal(code) {
  return /^\d{6}$/.test(String(code ?? '').replace(/\s+/g, ''))
    ? null
    : 'Mã xác nhận gồm 6 chữ số'
}

/** Why a password is refused before any request, or null when it can be sent. */
export function passwordRefusal(password, minChars) {
  return String(password ?? '').length >= minChars
    ? null
    : `Mật khẩu cần ít nhất ${minChars} ký tự`
}

/** The Vietnamese message shown for a failed call to the /auth routes. */
export function authErrorMessage(status) {
  switch (status) {
    case NETWORK_FAILURE: return 'Không kết nối được máy chủ'
    case 401: return 'Sai email hoặc mật khẩu'
    case 403: return 'Máy này không cho phép tạo tài khoản'
    case 404: return 'Mục này không còn tồn tại'
    case 409: return 'Email này đã có tài khoản, hãy đăng nhập'
    case 422: return 'Email hoặc mật khẩu không hợp lệ'
    case 429: return 'Đăng nhập sai quá nhiều lần, vui lòng thử lại sau ít phút'
    case 502: return 'Máy chủ đang gặp sự cố, vui lòng thử lại'
    default: return `Lỗi máy chủ (${status})`
  }
}

/**
 * The message for a route that acts for the signed-in user (the linked accounts): 401
 * there is a session that is over, not a wrong password. `other` names the rest.
 */
export function sessionErrorMessage(status, other = authErrorMessage) {
  return status === 401 ? 'Phiên đăng nhập đã hết, vui lòng đăng nhập lại' : other(status)
}

/**
 * The message for a refused request of a code: 429 and 502 mean something else there
 * (a code was just sent, or too many were asked for; the mail could not be sent).
 */
export function registerErrorMessage(status) {
  switch (status) {
    case 429: return 'Mã vừa được gửi hoặc đã được yêu cầu quá nhiều lần, vui lòng đợi rồi thử lại'
    case 502: return 'Chưa gửi được mã tới email này, vui lòng thử lại'
    default: return authErrorMessage(status)
  }
}

/** The message for a refused code: 401 is a wrong code, 410 one that is over. */
export function verifyErrorMessage(status) {
  switch (status) {
    case 401: return 'Mã không đúng, vui lòng kiểm tra lại'
    case 410: return 'Mã đã hết hạn hoặc nhập sai quá nhiều lần, vui lòng yêu cầu mã mới'
    case 409: return 'Email này đã có tài khoản, hãy đăng nhập'
    default: return authErrorMessage(status)
  }
}

/** What the form says once a code was sent ({ email, expires_in_sec }). */
export function codeSentMessage(sent) {
  const minutes = Math.max(1, Math.round((Number(sent?.expires_in_sec) || 0) / 60))
  return `Đã gửi mã gồm 6 chữ số tới ${sent?.email}. Mã có hiệu lực trong ${minutes} phút.`
}

/**
 * How long the form keeps what waits for a code ({ expires_in_sec }), in milliseconds:
 * as long as the code can be given back. An answer without a duration keeps nothing.
 */
export function forgetDelayMs(sent) {
  const seconds = Number(sent?.expires_in_sec)
  return Number.isFinite(seconds) && seconds > 0 ? Math.round(seconds * 1000) : 0
}

/** What the form says once the code is over and what was typed is forgotten. */
export function codeExpiredMessage() {
  return 'Mã đã hết hạn, vui lòng nhập lại email và mật khẩu để nhận mã mới'
}

/** The message for a refused unlinking: 409 means something else there. */
export function unlinkErrorMessage(status) {
  return status === 409
    ? 'Đây là cách đăng nhập duy nhất của bạn, không gỡ được'
    : authErrorMessage(status)
}

/**
 * The message for a refused confirmation of a link (`POST /auth/identities`): the
 * person is back from the provider and the page asks to attach who they proved to be.
 * 409 has two causes the page cannot tell apart (the identity belongs to another user;
 * this user already has one of that provider): one message covers both.
 */
export function linkErrorMessage(status) {
  switch (status) {
    case 403: return 'Yêu cầu liên kết này thuộc về một tài khoản khác, vui lòng thử lại'
    case 409: return 'Không liên kết được: tài khoản này đã được liên kết'
    case 410: return 'Yêu cầu liên kết đã hết hạn, vui lòng thử lại'
    case 422: return 'Yêu cầu liên kết không hợp lệ, vui lòng thử lại'
    default: return authErrorMessage(status)
  }
}

const PROVIDER_LABELS = { google: 'Google', facebook: 'Facebook' }

/** How a provider is named on the page; an unknown one as it is. */
export function providerLabel(provider) {
  return PROVIDER_LABELS[provider] ?? String(provider)
}

/**
 * What the backend left in the address after a sign-in at a provider
 * (`#auth_code=…`, `#auth_link=…`, `#auth_error=…`): { code } to exchange for a
 * session, { link } to confirm a link with, { error }, or null when the address holds
 * none of them.
 */
export function readFragment(hash) {
  const params = new URLSearchParams(String(hash ?? '').replace(/^#/, ''))
  for (const [key, name] of [['auth_code', 'code'], ['auth_link', 'link'], ['auth_error', 'error']]) {
    const value = params.get(key)
    if (value) return { [name]: value }
  }
  return null
}

// ── The verifier that binds a sign-in at a provider to the page that started it ──

/** Bytes → base64url without padding (RFC 4648 §5). */
export function base64url(bytes) {
  let binary = ''
  for (const byte of new Uint8Array(bytes)) binary += String.fromCharCode(byte)
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
}

/** A fresh random value of `byteCount` bytes, as base64url: a verifier, a frame id. */
export function randomToken(byteCount, random = globalThis.crypto) {
  return base64url(random.getRandomValues(new Uint8Array(byteCount)))
}

/**
 * The challenge of a verifier: base64url(SHA-256(verifier)), the verifier taken as the
 * ASCII text it is. The backend is given the challenge when the sign-in starts and the
 * verifier when its outcome is collected.
 */
export async function challengeOf(verifier, subtle = globalThis.crypto?.subtle) {
  if (!subtle) throw new Error('SHA-256 is not available on this page')
  return base64url(await subtle.digest('SHA-256', new TextEncoder().encode(verifier)))
}

/** The message for a sign-in at a provider that did not go through. */
export function providerErrorMessage(reason) {
  switch (reason) {
    case 'denied': return 'Bạn đã huỷ đăng nhập'
    case 'link_expired': return 'Yêu cầu liên kết đã hết hạn, vui lòng thử lại'
    case 'provider': return 'Nhà cung cấp không phản hồi, vui lòng thử lại'
    default: return 'Đăng nhập không thành công, vui lòng thử lại'
  }
}

/** What the page says once a provider was linked to the account. */
export function linkedMessage(provider) {
  return `Đã liên kết tài khoản ${providerLabel(provider)}`
}

/** The line of a linked account: "Google · linh@example.com", or the provider alone. */
export function identityLabel(identity) {
  const label = providerLabel(identity.provider)
  return identity.email ? `${label} · ${identity.email}` : label
}

/** The providers a signed-in user can still link: the offered ones they have not. */
export function linkableProviders(offered, identities) {
  const linked = new Set((identities ?? []).map((identity) => identity.provider))
  return (offered ?? []).filter((provider) => !linked.has(provider))
}

/** How the mode is shown beside the "Hồ sơ" button. */
export function modeLabel(user) {
  return user ? (user.display_name || 'Đã đăng nhập') : 'Khách'
}
