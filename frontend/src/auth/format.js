/**
 * Pure helpers of the sign-in (unit-tested, no DOM, no network).
 */

import { NETWORK_FAILURE } from '../memory/format.js'

/** The headers that present a token to the backend; none for a guest. */
export function authHeaders(token) {
  return token ? { Authorization: `Bearer ${token}` } : {}
}

const USERNAME = /^[a-z0-9._-]{3,32}$/

/**
 * Why a user name is refused before any request, or null when it can be sent: the
 * backend would refuse it anyway (it compares names without case).
 */
export function usernameRefusal(username) {
  return USERNAME.test(String(username ?? '').trim().toLowerCase())
    ? null
    : 'Tên đăng nhập gồm 3 đến 32 ký tự: chữ không dấu, số, dấu chấm, gạch dưới hoặc gạch ngang'
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
    case 401: return 'Sai tên đăng nhập hoặc mật khẩu'
    case 403: return 'Máy này không cho phép tạo tài khoản'
    case 404: return 'Mục này không còn tồn tại'
    case 409: return 'Tên đăng nhập này đã có người dùng'
    case 422: return 'Tên đăng nhập hoặc mật khẩu không hợp lệ'
    case 429: return 'Đăng nhập sai quá nhiều lần, vui lòng thử lại sau ít phút'
    case 502: return 'Máy chủ đang gặp sự cố, vui lòng thử lại'
    default: return `Lỗi máy chủ (${status})`
  }
}

/** The message for a refused unlinking: 409 means something else there. */
export function unlinkErrorMessage(status) {
  return status === 409
    ? 'Đây là cách đăng nhập duy nhất của bạn, không gỡ được'
    : authErrorMessage(status)
}

const PROVIDER_LABELS = { google: 'Google', facebook: 'Facebook' }

/** How a provider is named on the page; an unknown one as it is. */
export function providerLabel(provider) {
  return PROVIDER_LABELS[provider] ?? String(provider)
}

/**
 * What the backend left in the address after a sign-in at a provider
 * (`#auth_code=…`, `#auth_linked=…`, `#auth_error=…`): { code }, { linked },
 * { error }, or null when the address holds none of them.
 */
export function readFragment(hash) {
  const params = new URLSearchParams(String(hash ?? '').replace(/^#/, ''))
  for (const [key, name] of [['auth_code', 'code'], ['auth_linked', 'linked'], ['auth_error', 'error']]) {
    const value = params.get(key)
    if (value) return { [name]: value }
  }
  return null
}

/** The message for a sign-in at a provider that did not go through. */
export function providerErrorMessage(reason) {
  switch (reason) {
    case 'denied': return 'Bạn đã huỷ đăng nhập'
    case 'linked_elsewhere': return 'Tài khoản này đã được liên kết với một người dùng khác'
    case 'already_linked': return 'Bạn đã liên kết một tài khoản khác của nhà cung cấp này'
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
