/**
 * The sign-in on the page: the mode shown beside the "Hồ sơ" button (a guest, or who is
 * signed in), the sign-in form a guest finds on the profile page, and the accounts
 * linked to a signed-in user.
 *
 * The frames are in index.html (#session-chip, #auth-form, #auth-identities). What is
 * shown follows the session of the page (./session.js): this panel changes the session
 * and redraws when it changes, whoever changed it. The texts come from the person and
 * from the providers: they are only ever written with textContent.
 */

import { AUTH_PASSWORD_MIN_CHARS } from '../config.js'
import {
  identityLabel,
  linkableProviders,
  linkedMessage,
  modeLabel,
  passwordRefusal,
  providerErrorMessage,
  providerLabel,
  readFragment,
  usernameRefusal,
} from './format.js'

function button(text, className) {
  const element = document.createElement('button')
  element.type = 'button'
  element.className = className
  element.textContent = text
  return element
}

export class AuthPanel {
  /** `openProfile()` shows the profile page, where a guest finds the form. */
  constructor(client, session, openProfile) {
    this._client = client
    this._session = session
    this._openProfile = openProfile
    this._mode = document.getElementById('session-mode')
    this._action = document.getElementById('session-action')
    this._username = document.getElementById('auth-username')
    this._password = document.getElementById('auth-password')
    this._login = document.getElementById('auth-login')
    this._register = document.getElementById('auth-register')
    this._providers = document.getElementById('auth-providers')
    this._message = document.getElementById('auth-message')
    this._identities = document.getElementById('auth-identities')
    this._identityList = document.getElementById('auth-identity-list')
    this._identityMessage = document.getElementById('auth-identity-message')
    this._offered = { providers: [], registration: false }
    this._busy = false
  }

  async init() {
    this._action.addEventListener('click', () => {
      if (this._session.user) this._signOut()
      else this._openProfile()
    })
    this._login.addEventListener('click', () => this._submit(false))
    this._register.addEventListener('click', () => this._submit(true))
    for (const field of [this._username, this._password]) {
      field.addEventListener('keydown', (event) => {
        if (event.isComposing) return   // the key belongs to the input method (Telex, VNI)
        if (event.key === 'Enter') this._submit(false)
      })
    }
    this._session.onChange(() => this._render())
    this._render()

    // What a provider's callback left in the address is read once and removed from it.
    const left = readFragment(window.location.hash)
    if (left) history.replaceState(null, '', window.location.pathname + window.location.search)

    try {
      this._offered = await this._client.offered()
    } catch {
      // The form still signs in with a user name; nothing else is offered.
    }
    this._render()
    if (left) await this._arrive(left)
    else if (this._session.token) await this._confirm()
  }

  /** The accounts linked to the user are read again: the profile page was opened. */
  refresh() {
    this._sayIdentity('')
    this._renderIdentities()
  }

  // ── Changes ─────────────────────────────────────────────────────────────

  /** Ask the backend whether the token the page kept still signs someone in. */
  async _confirm() {
    try {
      this._session.confirm(await this._client.user())
    } catch {
      // No answer: the page keeps what it has; the WebSocket will say who it is.
    }
  }

  /** The person is back from a provider. */
  async _arrive({ code, linked, error }) {
    if (code) {
      try {
        this._session.signIn(await this._client.exchange(code))
      } catch (err) {
        this._say(providerErrorMessage(err.status === 401 ? 'expired' : 'provider'), true)
        this._openProfile()
      }
      return
    }
    await this._confirm()
    this._openProfile()
    if (linked) this._sayIdentity(linkedMessage(linked))
    else if (this._session.user) this._sayIdentity(providerErrorMessage(error), true)
    else this._say(providerErrorMessage(error), true)
  }

  async _submit(creating) {
    if (this._busy) return
    const username = this._username.value.trim()
    const password = this._password.value
    const refusal = usernameRefusal(username)
      || (creating ? passwordRefusal(password, AUTH_PASSWORD_MIN_CHARS) : null)
      || (password ? null : 'Vui lòng nhập mật khẩu')
    if (refusal) return this._say(refusal, true)

    this._setBusy(true)
    this._say(creating ? 'Đang tạo tài khoản…' : 'Đang đăng nhập…')
    try {
      const session = creating
        ? await this._client.register(username, password)
        : await this._client.login(username, password)
      this._password.value = ''
      this._say('')
      this._session.signIn(session)
    } catch (err) {
      this._say(err.message, true)
    }
    this._setBusy(false)
  }

  async _signOut() {
    try {
      await this._client.logout()
    } catch {
      // The page forgets its token whatever the backend answered.
    }
    this._session.signOut()
  }

  async _link(provider) {
    try {
      window.location.assign(await this._client.linkUrl(provider))
    } catch (err) {
      this._sayIdentity(err.message, true)
    }
  }

  async _unlink(provider) {
    try {
      await this._client.unlink(provider)
      this._sayIdentity(`Đã gỡ tài khoản ${providerLabel(provider)}`)
    } catch (err) {
      this._sayIdentity(err.message, true)
    }
    this._renderIdentities()
  }

  _setBusy(busy) {
    this._busy = busy
    for (const control of [this._username, this._password, this._login, this._register]) {
      control.disabled = busy
    }
  }

  // ── Rendering ───────────────────────────────────────────────────────────

  _render() {
    const user = this._session.user
    this._mode.textContent = modeLabel(user)
    this._action.textContent = user ? 'Đăng xuất' : 'Đăng nhập'
    this._register.hidden = !this._offered.registration
    this._providers.replaceChildren(...this._offered.providers.map((provider) => {
      const start = button(`Đăng nhập với ${providerLabel(provider)}`, 'profile-button')
      start.addEventListener('click', () => {
        window.location.assign(this._client.startUrl(provider))
      })
      return start
    }))
    this._identities.hidden = !user || this._offered.providers.length === 0
    if (user) this._renderIdentities()
  }

  /** The linked accounts, then one "Liên kết" per provider that is not linked yet. */
  async _renderIdentities() {
    if (!this._session.user || this._offered.providers.length === 0) return
    let linked
    try {
      linked = await this._client.identities()
    } catch (err) {
      return this._sayIdentity(err.message, true)
    }
    const rows = linked.map((identity) => {
      const row = document.createElement('li')
      row.className = 'profile-item'
      const text = document.createElement('div')
      text.className = 'profile-item-content'
      text.textContent = identityLabel(identity)
      const remove = button('Gỡ', 'profile-button danger')
      remove.addEventListener('click', () => {
        remove.disabled = true
        this._unlink(identity.provider)
      })
      row.append(text, remove)
      return row
    })
    for (const provider of linkableProviders(this._offered.providers, linked)) {
      const row = document.createElement('li')
      row.className = 'profile-item'
      const text = document.createElement('div')
      text.className = 'profile-item-content profile-empty'
      text.textContent = `${providerLabel(provider)} · chưa liên kết`
      const link = button('Liên kết', 'profile-button primary')
      link.addEventListener('click', () => {
        link.disabled = true
        this._link(provider)
      })
      row.append(text, link)
      rows.push(row)
    }
    this._identityList.replaceChildren(...rows)
  }

  _say(text, isError = false) {
    this._message.textContent = text
    this._message.classList.toggle('error', isError)
  }

  _sayIdentity(text, isError = false) {
    this._identityMessage.textContent = text
    this._identityMessage.classList.toggle('error', isError)
  }
}
