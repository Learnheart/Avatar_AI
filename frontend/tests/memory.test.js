/**
 * Unit tests for memory/format.js — the pure helpers of the profile page.
 * Run: npm test
 */

import { test } from 'node:test'
import assert from 'node:assert/strict'

import {
  CATEGORIES,
  NETWORK_FAILURE,
  STATUSES,
  UNAUTHORIZED,
  apiErrorMessage,
  approvedMessage,
  categoryLabel,
  fileRefusal,
  formatDate,
  groupItems,
  importErrorMessage,
  importLabel,
  importedMessage,
  itemCountLabel,
  itemSourceLabel,
  proposals,
  proposalsHeading,
  rejectedMessage,
  replacedItem,
  sourceLabel,
} from '../src/memory/format.js'

const EXTENSIONS = ['.txt', '.md', '.csv', '.pdf', '.docx']
const MB = 1024 * 1024

function item(id, category, status = 'active', source = 'manual') {
  return { id, category, status, source, text: `item ${id}` }
}

function ids(list) {
  return list.map((entry) => entry.id)
}

test('groupItems puts each item under its status and its category', () => {
  const groups = groupItems([
    item('a', 'identity'),
    item('b', 'preference'),
    item('c', 'preference', 'pending', 'conversation'),
    item('d', 'addressing'),
    item('e', 'personality', 'pending', 'conversation'),
  ])

  assert.deepEqual(ids(groups.active.identity), ['a'])
  assert.deepEqual(ids(groups.active.addressing), ['d'])
  assert.deepEqual(ids(groups.active.preference), ['b'])
  assert.deepEqual(ids(groups.active.personality), [])
  assert.deepEqual(ids(groups.pending.preference), ['c'])
  assert.deepEqual(ids(groups.pending.personality), ['e'])
  assert.deepEqual(ids(groups.pending.identity), [])
})

test('groupItems keeps the order of the API inside a list', () => {
  const groups = groupItems([
    item('3', 'preference'),
    item('1', 'preference'),
    item('2', 'preference'),
  ])

  assert.deepEqual(ids(groups.active.preference), ['3', '1', '2'])
})

test('groupItems returns every status and category when there is no item', () => {
  for (const items of [[], undefined, null]) {
    const groups = groupItems(items)

    assert.deepEqual(Object.keys(groups), STATUSES)
    for (const status of STATUSES) {
      assert.deepEqual(Object.keys(groups[status]), CATEGORIES)
      for (const category of CATEGORIES) assert.deepEqual(groups[status][category], [])
    }
  }
})

test('groupItems leaves out an item of an unknown status or category', () => {
  const groups = groupItems([
    item('a', 'identity'),
    item('b', 'identity', 'rejected'),
    item('c', 'hobby'),
    item('d', 'constructor'),
    item('e', 'identity', 'constructor'),
  ])

  assert.deepEqual(ids(groups.active.identity), ['a'])
  const kept = STATUSES.flatMap((status) => CATEGORIES.flatMap((c) => groups[status][c]))
  assert.equal(kept.length, 1)
})

test('categoryLabel names the four categories in Vietnamese', () => {
  assert.equal(categoryLabel('identity'), 'Thông tin về bạn')
  assert.equal(categoryLabel('addressing'), 'Cách xưng hô')
  assert.equal(categoryLabel('preference'), 'Sở thích')
  assert.equal(categoryLabel('personality'), 'Tính cách')
})

test('categoryLabel shows an unknown category as it is', () => {
  assert.equal(categoryLabel('hobby'), 'hobby')
})

test('sourceLabel tells typed, imported and heard apart', () => {
  const labels = ['manual', 'import', 'conversation'].map(sourceLabel)

  assert.equal(labels[0], 'Tự nhập')
  assert.match(labels[1], /nhập/)
  assert.match(labels[2], /trò chuyện/)
  assert.equal(new Set(labels).size, 3)
  assert.equal(sourceLabel('other'), 'other')
})

test('apiErrorMessage explains each refusal of the memory API', () => {
  assert.match(apiErrorMessage(UNAUTHORIZED), /đăng nhập/)
  assert.match(apiErrorMessage(404), /không còn tồn tại/)
  assert.match(apiErrorMessage(422), /không hợp lệ/)
  assert.match(apiErrorMessage(502), /thử lại/)
  assert.equal(UNAUTHORIZED, 401)
})

test('apiErrorMessage has a message for a request that got no answer', () => {
  assert.equal(apiErrorMessage(NETWORK_FAILURE), 'Không kết nối được máy chủ')
})

test('apiErrorMessage falls back to the status for any other failure', () => {
  assert.equal(apiErrorMessage(500), 'Lỗi máy chủ (500)')
})

test('apiErrorMessage gives a different message for each known failure', () => {
  const messages = [NETWORK_FAILURE, UNAUTHORIZED, 404, 422, 502, 500].map(apiErrorMessage)

  assert.equal(new Set(messages).size, messages.length)
})

// ── Imports ─────────────────────────────────────────────────────────────────

test('importErrorMessage explains a file that is too large or of another type', () => {
  assert.match(importErrorMessage(413, 5), /5 MB/)
  assert.match(importErrorMessage(415, 5), /Định dạng/)
  assert.notEqual(importErrorMessage(413, 5), importErrorMessage(415, 5))
})

test('importErrorMessage has its own words for an unreadable text and a failed reading', () => {
  assert.match(importErrorMessage(422, 5), /Không đọc được|không có gì/)
  assert.notEqual(importErrorMessage(422, 5), apiErrorMessage(422))
  assert.match(importErrorMessage(502, 5), /thử lại/)
  assert.notEqual(importErrorMessage(502, 5), apiErrorMessage(502))
})

test('importErrorMessage says the same as the other calls for the rest', () => {
  for (const status of [NETWORK_FAILURE, UNAUTHORIZED, 404, 500]) {
    assert.equal(importErrorMessage(status, 5), apiErrorMessage(status))
  }
})

test('fileRefusal lets through a file of a supported type within the limit', () => {
  assert.equal(fileRefusal({ name: 'cv.pdf', size: 1000 }, EXTENSIONS, 5), null)
  assert.equal(fileRefusal({ name: 'GIOI-THIEU.DOCX', size: 5 * MB }, EXTENSIONS, 5), null)
})

test('fileRefusal refuses another type, then a file over the limit', () => {
  assert.match(fileRefusal({ name: 'photo.png', size: 10 }, EXTENSIONS, 5), /Định dạng/)
  assert.match(fileRefusal({ name: 'pdf', size: 10 }, EXTENSIONS, 5), /Định dạng/)
  assert.match(fileRefusal({ name: 'cv.pdf', size: 5 * MB + 1 }, EXTENSIONS, 5), /5 MB/)
  assert.match(fileRefusal({ name: 'big.png', size: 9 * MB }, EXTENSIONS, 5), /Định dạng/)
})

test('itemCountLabel and importedMessage say how many items an import produced', () => {
  assert.equal(itemCountLabel(3), '3 mục')
  assert.equal(itemCountLabel(0), 'không có mục nào')
  assert.equal(itemCountLabel(undefined), 'không có mục nào')
  assert.equal(importedMessage(4), 'Đã thêm 4 mục vào hồ sơ')
})

test('formatDate gives the day in the time of the browser, and nothing when unreadable', () => {
  assert.equal(formatDate(new Date(2026, 9, 2, 12).toISOString()), '02/10/2026')
  assert.equal(formatDate('not a date'), '')
  assert.equal(formatDate(null), '')
})

test('importLabel shows the size, the number of items and the date of an import', () => {
  const created_at = new Date(2026, 9, 12, 12).toISOString()

  assert.equal(
    importLabel({ size_bytes: 1536, item_count: 3, created_at }),
    '1.5 KB · 3 mục · 12/10/2026',
  )
  assert.equal(importLabel({ size_bytes: 20, item_count: 0 }), '20 B · không có mục nào')
})

test('itemSourceLabel names the import of an item while that import is listed', () => {
  const imports = [{ id: 'i1', name: 'cv.pdf' }, { id: 'i2', name: 'ghi chú.txt' }]
  const imported = { source: 'import', import_id: 'i2' }

  assert.equal(itemSourceLabel(imported, imports), `${sourceLabel('import')} · ghi chú.txt`)
  assert.equal(itemSourceLabel(imported, [imports[0]]), sourceLabel('import'))
  assert.equal(itemSourceLabel(imported, undefined), sourceLabel('import'))
  assert.equal(itemSourceLabel({ source: 'import', import_id: null }, imports), sourceLabel('import'))
  assert.equal(itemSourceLabel({ source: 'manual', import_id: null }, imports), 'Tự nhập')
})

// ── Proposals ───────────────────────────────────────────────────────────────

function proposal(id, created_at, extra = {}) {
  return { ...item(id, 'preference', 'pending', 'conversation'), created_at, ...extra }
}

test('proposals are the pending items, the oldest first, whatever their category', () => {
  const listed = proposals([
    item('a', 'identity'),
    proposal('new', '2026-10-13T09:00:00+00:00'),
    proposal('old', '2026-10-11T09:00:00+00:00', { category: 'identity' }),
    proposal('mid', '2026-10-12T09:00:00+00:00', { category: 'personality' }),
    { ...item('r', 'preference', 'rejected', 'conversation'), created_at: '2026-10-01T09:00:00Z' },
  ])

  assert.deepEqual(ids(listed), ['old', 'mid', 'new'])
})

test('proposals compare dates as times, not as texts', () => {
  const listed = proposals([
    proposal('later', '2026-10-12T09:00:00+00:00'),
    proposal('earlier', '2026-10-12T10:00:00+07:00'),   // 03:00 UTC
  ])

  assert.deepEqual(ids(listed), ['earlier', 'later'])
})

test('proposals without a readable date, or of the same time, keep the order of the API', () => {
  const same = '2026-10-12T09:00:00Z'
  const listed = proposals([
    proposal('x', undefined),
    proposal('b', same, { category: 'personality' }),
    proposal('y', 'not a date'),
    proposal('a', same, { category: 'identity' }),
  ])

  assert.deepEqual(ids(listed), ['b', 'a', 'x', 'y'])
})

test('proposals are empty when nothing is pending', () => {
  assert.deepEqual(proposals([item('a', 'identity')]), [])
  assert.deepEqual(proposals([]), [])
  assert.deepEqual(proposals(undefined), [])
})

test('replacedItem is the active item a proposal would replace', () => {
  const old = item('a', 'preference')
  const items = [old, item('b', 'identity'), proposal('p', undefined, { replaces_id: 'a' })]

  assert.equal(replacedItem(items[2], items), old)
})

test('replacedItem is null for a plain proposal and when the item is no longer active', () => {
  const other = proposal('q', undefined)
  const items = [item('a', 'preference'), other]

  assert.equal(replacedItem(proposal('p', undefined, { replaces_id: null }), items), null)
  assert.equal(replacedItem(proposal('p', undefined), items), null)
  assert.equal(replacedItem(proposal('p', undefined, { replaces_id: 'gone' }), items), null)
  // Another proposal is not something to replace.
  assert.equal(replacedItem(proposal('p', undefined, { replaces_id: 'q' }), items), null)
  assert.equal(replacedItem(proposal('p', undefined, { replaces_id: 'a' }), undefined), null)
})

test('proposalsHeading says they are heard, not confirmed, and how many', () => {
  assert.match(proposalsHeading(3), /nghe được/)
  assert.match(proposalsHeading(3), /chưa xác nhận/)
  assert.match(proposalsHeading(3), /3/)
  assert.match(proposalsHeading(1), /1/)
})

test('the messages after a proposal tell approve, replace and reject apart', () => {
  const messages = [approvedMessage(false), approvedMessage(true), rejectedMessage()]

  assert.match(messages[0], /ghi nhớ/)
  assert.match(messages[1], /thay/)
  assert.match(messages[2], /bỏ qua/)
  assert.equal(new Set(messages).size, 3)
})
