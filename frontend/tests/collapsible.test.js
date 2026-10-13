/**
 * Unit tests for ui/collapsible.js — what a drop-down remembers.
 * Run: npm test
 */

import { test } from 'node:test'
import assert from 'node:assert/strict'

import { keepOpen, storedOpen } from '../src/ui/collapsible.js'

class FakeStorage {
  constructor(items = {}) { this.items = { ...items } }
  getItem(key) { return this.items[key] ?? null }
  setItem(key, value) { this.items[key] = String(value) }
}

test('a drop-down nothing is kept about starts as it is told to', () => {
  const storage = new FakeStorage()

  assert.equal(storedOpen(storage, 'panel', true), true)
  assert.equal(storedOpen(storage, 'panel', false), false)
})

test('a drop-down is found again as it was left', () => {
  const storage = new FakeStorage()

  keepOpen(storage, 'panel', false)
  assert.equal(storedOpen(storage, 'panel', true), false)
  keepOpen(storage, 'panel', true)
  assert.equal(storedOpen(storage, 'panel', false), true)
})

test('each drop-down is remembered on its own', () => {
  const storage = new FakeStorage()

  keepOpen(storage, 'documents', true)
  keepOpen(storage, 'transcript', false)

  assert.equal(storedOpen(storage, 'documents', false), true)
  assert.equal(storedOpen(storage, 'transcript', true), false)
})

test('something else kept under the name is ignored', () => {
  const storage = new FakeStorage({ panel: 'true' })

  assert.equal(storedOpen(storage, 'panel', false), false)
  assert.equal(storedOpen(storage, 'panel', true), true)
})

test('without a storage, or with one that fails, the start is used and nothing breaks', () => {
  const broken = {
    getItem() { throw new Error('denied') },
    setItem() { throw new Error('full') },
  }

  assert.equal(storedOpen(null, 'panel', true), true)
  assert.equal(storedOpen(broken, 'panel', false), false)
  keepOpen(null, 'panel', true)
  keepOpen(broken, 'panel', true)
})
