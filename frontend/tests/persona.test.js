/**
 * Unit tests for persona/format.js — the pure helpers of the "Tính cách của avatar" block.
 * Run: npm test
 */

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

import { NETWORK_FAILURE, UNAUTHORIZED } from '../src/memory/format.js'
import {
  PERSONA_FIELDS,
  clearedMessage,
  defaultHint,
  formValues,
  mergeRead,
  personaErrorMessage,
  readErrorMessage,
  readMessage,
  sameFields,
  savedMessage,
} from '../src/persona/format.js'

test('the fields of the page are the fields of the backend, in its order', () => {
  const models = readFileSync(new URL('../../backend/models.py', import.meta.url), 'utf8')
  const declared = models.match(/^PERSONA_FIELDS = \(([^)]*)\)/m)[1]
  const backend = [...declared.matchAll(/"(\w+)"/g)].map((match) => match[1])

  assert.deepEqual(PERSONA_FIELDS.map((field) => field.key), backend)
})

test('every field has a label and a placeholder', () => {
  for (const field of PERSONA_FIELDS) {
    assert.ok(field.label && field.placeholder, field.key)
  }
})

test('formValues trims the texts and leaves out the empty fields', () => {
  assert.deepEqual(
    formValues({ name: '  Mai ', role: '', addressing: '   ', avoid: 'Chính trị' }),
    { name: 'Mai', avoid: 'Chính trị' },
  )
})

test('formValues keeps the order of the fields and drops what is not one', () => {
  const values = formValues({ avoid: 'x', voice: 'trầm', name: 'Mai', role: 7 })

  assert.deepEqual(Object.keys(values), ['name', 'avoid'])
})

test('formValues of nothing is empty', () => {
  assert.deepEqual(formValues(undefined), {})
  assert.deepEqual(formValues({}), {})
})

test('mergeRead overwrites only the fields the reading found', () => {
  const form = { name: 'Lan', role: 'Gia sư', avoid: ' ' }
  const read = { name: 'Mai', addressing: 'xưng em' }

  assert.deepEqual(mergeRead(form, read), { name: 'Mai', role: 'Gia sư', addressing: 'xưng em' })
})

test('mergeRead keeps a typed field when the reading gives it empty', () => {
  assert.deepEqual(mergeRead({ name: 'Lan' }, { name: '  ' }), { name: 'Lan' })
})

test('sameFields compares the texts as they would be saved', () => {
  assert.equal(sameFields({ name: 'Mai ', role: '' }, { name: 'Mai' }), true)
  assert.equal(sameFields({}, undefined), true)
  assert.equal(sameFields({ name: 'Mai' }, { name: 'Lan' }), false)
  assert.equal(sameFields({ name: 'Mai' }, { name: 'Mai', avoid: 'x' }), false)
})

test('defaultHint names the default of a field, and nothing for a field without one', () => {
  const defaults = { personality: ' Thân thiện ', role: '' }

  assert.equal(defaultHint(defaults, 'personality'), 'Để trống thì avatar dùng mặc định: Thân thiện')
  assert.equal(defaultHint(defaults, 'role'), '')
  assert.equal(defaultHint(defaults, 'name'), '')
  assert.equal(defaultHint(null, 'personality'), '')
})

test('the messages of the block', () => {
  assert.match(readMessage(3), /Đã điền 3 trường/)
  assert.match(savedMessage(2), /^Đã lưu/)
  assert.match(savedMessage(0), /mặc định/)
  assert.match(clearedMessage(), /^Đã xoá/)
})

test('personaErrorMessage names each refusal', () => {
  assert.equal(personaErrorMessage(NETWORK_FAILURE, 300), 'Không kết nối được máy chủ')
  assert.match(personaErrorMessage(UNAUTHORIZED, 300), /đăng nhập/)
  assert.match(personaErrorMessage(413, 300), /quá dài/)
  assert.match(personaErrorMessage(422, 300), /tối đa 300 ký tự/)
  assert.match(personaErrorMessage(502, 300), /thử lại/)
  assert.equal(personaErrorMessage(500, 300), 'Lỗi máy chủ (500)')
})

test('readErrorMessage has its own 422 and 502 and shares the rest', () => {
  assert.match(readErrorMessage(422, 300), /Không thấy điều gì/)
  assert.match(readErrorMessage(502, 300), /Chưa đọc được/)
  assert.equal(readErrorMessage(413, 300), personaErrorMessage(413, 300))
  assert.equal(readErrorMessage(NETWORK_FAILURE, 300), personaErrorMessage(NETWORK_FAILURE, 300))
})
