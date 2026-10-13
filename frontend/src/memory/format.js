/**
 * Pure helpers of the profile page (unit-tested, no DOM, no network).
 */

import { formatBytes, isSupportedFile } from '../knowledge/format.js'

/** The categories of a memory item, in the order the profile page shows them. */
export const CATEGORIES = ['identity', 'addressing', 'preference', 'personality']

/** `active`: given to the LLM. `pending`: gathered by the avatar, not confirmed yet. */
export const STATUSES = ['active', 'pending']

/** What apiErrorMessage() takes as status when the request never got an answer. */
export const NETWORK_FAILURE = 0

/** The status of a guest: nobody is logged in. */
export const UNAUTHORIZED = 401

/**
 * The status of a request whose answer came after the person on the page changed
 * (auth/session.js): its outcome is shown to nobody.
 */
export const SUPERSEDED = -1

const CATEGORY_LABELS = {
  identity: 'Thông tin về bạn',
  addressing: 'Cách xưng hô',
  preference: 'Sở thích',
  personality: 'Tính cách',
}

const SOURCE_LABELS = {
  manual: 'Tự nhập',
  import: 'Từ tệp đã nhập',
  conversation: 'Nghe được khi trò chuyện',
}

/**
 * Items of the API → { active: { identity: [...], ... }, pending: { ... } }.
 *
 * Every status holds every category (an empty list when it has no item), and the
 * order of the API is kept inside a list. An item of an unknown status or category is
 * left out: the page has no place to show it.
 */
export function groupItems(items) {
  const groups = {}
  for (const status of STATUSES) {
    groups[status] = {}
    for (const category of CATEGORIES) groups[status][category] = []
  }
  for (const item of items ?? []) {
    const list = groups[item.status]?.[item.category]
    if (Array.isArray(list)) list.push(item)
  }
  return groups
}

/** The Vietnamese title of a category; an unknown one is shown as it is. */
export function categoryLabel(category) {
  return CATEGORY_LABELS[category] ?? String(category)
}

/** Where an item comes from: typed, imported or heard; an unknown source as it is. */
export function sourceLabel(source) {
  return SOURCE_LABELS[source] ?? String(source)
}

/**
 * Where an item comes from, with the name of its import while that import is still
 * listed: "Từ tệp đã nhập · cv.pdf".
 */
export function itemSourceLabel(item, imports) {
  const label = sourceLabel(item.source)
  if (item.import_id == null) return label
  const origin = (imports ?? []).find((entry) => entry.id === item.import_id)
  return origin ? `${label} · ${origin.name}` : label
}

/** The Vietnamese message shown for a failed call to the memory API. */
export function apiErrorMessage(status) {
  switch (status) {
    case NETWORK_FAILURE: return 'Không kết nối được máy chủ'
    case UNAUTHORIZED: return 'Cần đăng nhập để xem và sửa hồ sơ'
    case 404: return 'Mục này không còn tồn tại'
    case 422: return 'Nội dung không hợp lệ, vui lòng nhập lại'
    case 502: return 'Kho hồ sơ đang gặp sự cố, vui lòng thử lại'
    default: return `Lỗi máy chủ (${status})`
  }
}

/**
 * The message for a refused import. 413 and 415 only come from there (`maxFileMb` is
 * the size limit of a file); 422 and 502 mean something else than for an item (nothing
 * to read or to remember; the reading failed); the rest is the same.
 */
export function importErrorMessage(status, maxFileMb) {
  switch (status) {
    case 413: return `Tệp vượt quá ${maxFileMb} MB, hoặc văn bản quá dài`
    case 415: return 'Định dạng tệp không được hỗ trợ'
    case 422: return 'Không đọc được nội dung, hoặc không có gì về bạn để ghi nhớ trong đó'
    case 502: return 'Chưa đọc được bản mô tả, vui lòng thử lại'
    default: return apiErrorMessage(status)
  }
}

// ── Proposals ───────────────────────────────────────────────────────────────

/**
 * What the avatar gathered from the conversations and the person has not confirmed
 * (the `pending` items), the oldest first: the order they were heard in. Proposals
 * created at the same time, or without a readable date, keep the order of the API.
 */
export function proposals(items) {
  const time = (item) => {
    const created = Date.parse(item.created_at ?? '')
    return Number.isNaN(created) ? Infinity : created
  }
  return Object.values(groupItems(items).pending)
    .flat()
    .map((item) => ({ item, time: time(item), index: (items ?? []).indexOf(item) }))
    .sort((a, b) => (a.time === b.time ? a.index - b.index : a.time < b.time ? -1 : 1))
    .map((entry) => entry.item)
}

/**
 * The item a proposal would replace when it is approved: the active item named by its
 * `replaces_id`, or null when it replaces nothing or that item is no longer listed
 * (deleted meanwhile) — the proposal is then shown as a plain one.
 */
export function replacedItem(proposal, items) {
  if (proposal.replaces_id == null) return null
  const active = Object.values(groupItems(items).active).flat()
  return active.find((item) => item.id === proposal.replaces_id) ?? null
}

/** The title of the proposals block, with their number. */
export function proposalsHeading(count) {
  return `Avatar nghe được, chưa xác nhận (${count})`
}

/** What the page says after a proposal was approved; `replaced`: it took an item's place. */
export function approvedMessage(replaced) {
  return replaced ? 'Đã ghi nhớ, thay cho mục cũ' : 'Đã ghi nhớ'
}

/** What the page says after a proposal was rejected. */
export function rejectedMessage() {
  return 'Đã bỏ qua, avatar sẽ không đề xuất lại điều này'
}

// ── Imports ─────────────────────────────────────────────────────────────────

/**
 * Why a file ({ name, size }) is refused before any request, or null when it can be
 * sent: the backend would refuse another type or a larger file anyway.
 */
export function fileRefusal(file, extensions, maxFileMb) {
  if (!isSupportedFile(file.name, extensions)) return importErrorMessage(415, maxFileMb)
  if (file.size > maxFileMb * 1024 * 1024) return `Tệp vượt quá ${maxFileMb} MB`
  return null
}

/** How many items an import produced, as its row says it: "3 mục". */
export function itemCountLabel(count) {
  const total = Math.max(0, Number(count) || 0)
  return total === 0 ? 'không có mục nào' : `${total} mục`
}

/**
 * What the page says after an import: how many items it added to the profile (at least
 * one: the backend refuses a description with nothing to remember in it).
 */
export function importedMessage(count) {
  return `Đã thêm ${count} mục vào hồ sơ`
}

/** "12/10/2026" for an ISO timestamp, in the time of the browser; '' when unreadable. */
export function formatDate(iso) {
  const date = new Date(iso ?? '')
  if (Number.isNaN(date.getTime())) return ''
  const two = (value) => String(value).padStart(2, '0')
  return `${two(date.getDate())}/${two(date.getMonth() + 1)}/${date.getFullYear()}`
}

/** The line under the name of an import: "1.5 KB · 3 mục · 12/10/2026". */
export function importLabel(entry) {
  const parts = [formatBytes(entry.size_bytes), itemCountLabel(entry.item_count)]
  const date = formatDate(entry.created_at)
  if (date) parts.push(date)
  return parts.join(' · ')
}
