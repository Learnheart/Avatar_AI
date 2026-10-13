/**
 * The sign-in on the page: the mode shown beside the "Hồ sơ" button (a guest, or who is
 * signed in), the sign-in form a guest finds on the profile page, and the accounts
 * linked to a signed-in user. Creating an account takes two steps: the email and a
 * password, then the code the backend sent to that email. Only the email is sent at
 * the first step: the password waits in the memory of the page and goes with the code.
 *
 * Nothing of one person is left for the next: on every change of the session, and when
 * a code expires, the form forgets what was typed and what waits; the linked accounts
 * on screen are emptied when the user changes.
 *
 * The frames are in index.html (#session-chip, #auth-form, #auth-identities). What is
 * shown follows the session of the page (./session.js): this panel changes the session
 * and redraws when it changes, whoever changed it. The texts come from the person and
 * from the providers: they are only ever written with textContent.
 */

import { AUTH_PASSWORD_MIN_CHARS, AUTH_VERIFIER_BYTES } from '../config.js'
import { SUPERSEDED, UNAUTHORIZED } from '../memory/format.js'
import {
  challengeOf,
  codeExpiredMessage,
  codeRefusal,
  codeSentMessage,
  emailRefusal,
  forgetDelayMs,
  identityLabel,
  linkableProviders,
  linkedMessage,
  modeLabel,
  passwordRefusal,
  providerErrorMessage,
  providerLabel,
  randomToken,
  readFragment,
  sessionErrorMessage,
} from './format.js'
import { keepVerifier, takeVerifier } from './session.js'

/** Where the verifier of a provider sign-in waits: this tab only, gone when it closes. */
function tabStorage() {
  try {
    return globalThis.sessionStorage ?? null
  } catch {
    return null
  }
}

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
    this._email = document.getElementById('auth-email')
    this._password = document.getElementById('auth-password')
    this._credentials = document.getElementById('auth-credentials')
    this._codeStep = document.getElementById('auth-code-step')
    this._codeSent = document.getElementById('auth-code-sent')
    this._code = document.getElementById('auth-code')
    this._verify = document.getElementById('auth-verify')
    this._resend = document.getElementById('auth-resend')
    this._back = document.getElementById('auth-back')
    this._login = document.getElementById('auth-login')
    this._register = document.getElementById('auth-register')
    this._providers = document.getElementById('auth-providers')
    this._message = document.getElementById('auth-message')
    this._identities = document.getElementById('auth-identities')
    this._identityList = document.getElementById('auth-identity-list')
    this._identityMessage = document.getElementById('auth-identity-message')
    this._offered = { providers: [], registration: false }
    this._busy = false
    // The account waiting for its code: { email, password }. The password is held here
    // only, never in the hidden input, and no longer than the code lasts.
    this._waiting = null
    this._expiry = null      // the timer that forgets it when the code expires
    this._shownUserId = null // whose linked accounts the list holds
  }

  async init() {
    this._action.addEventListener('click', () => {
      if (this._session.user) this._signOut()
      else this._openProfile()
    })
    this._login.addEventListener('click', () => this._submit(false))
    this._register.addEventListener('click', () => this._submit(true))
    for (const field of [this._email, this._password]) {
      field.addEventListener('keydown', (event) => {
        if (event.isComposing) return   // the key belongs to the input method (Telex, VNI)
        if (event.key === 'Enter') this._submit(false)
      })
    }
    this._verify.addEventListener('click', () => this._giveCode())
    this._resend.addEventListener('click', () => this._askCode(this._waiting))
    this._back.addEventListener('click', () => this._showStep(null))
    this._code.addEventListener('keydown', (event) => {
      if (event.key === 'Enter') this._giveCode()
    })
    this._session.onChange(() => {
      this._forget()
      this._render()
    })
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

  // ── Changes ─────────────────────────────────────────────────────────────

  /**
   * Ask the backend whether the token the page kept still signs someone in. The answer
   * is about that token: it changes nothing when the page holds another one by then.
   */
  async _confirm() {
    const token = this._session.token
    try {
      this._session.confirm(token, await this._client.user())
    } catch {
      // No answer: the page keeps what it has; the WebSocket will say who it is.
    }
  }

  /**
   * The person is back from a provider. The verifier this page kept when it started is
   * taken out of the tab whatever came back: it proves one sign-in, also a failed one.
   */
  async _arrive({ code, link, error }) {
    const verifier = takeVerifier(tabStorage())
    if (code) return this._collect(code, verifier)
    if (link) return this._confirmLink(link)
    await this._confirm()
    this._openProfile()
    if (this._session.user) this._sayIdentity(providerErrorMessage(error), true)
    else this._say(providerErrorMessage(error), true)
  }

  /** Go to a provider to sign in, leaving here the verifier that proves who started. */
  async _start(provider) {
    try {
      const verifier = randomToken(AUTH_VERIFIER_BYTES)
      const challenge = await challengeOf(verifier)
      if (!keepVerifier(tabStorage(), verifier)) throw new Error('no storage for the verifier')
      window.location.assign(this._client.startUrl(provider, challenge))
    } catch {
      // No random source, no SHA-256 (a page not served over https or from localhost)
      // or no storage: the sign-in cannot be bound to this page, so it is not started.
      this._say(providerErrorMessage(), true)
    }
  }

  /**
   * Collect the sign-in a provider's callback left: only with the verifier this page
   * kept when it started. Without one, the code was not started here and is left alone.
   */
  async _collect(code, verifier) {
    try {
      if (!verifier) throw new Error('this page started no sign-in')
      const session = await this._client.exchange(code, verifier)
      this._forget()
      this._session.signIn(session)
    } catch (err) {
      if (err.status === SUPERSEDED) return
      // Refused (401: spent, expired, or not this page's) or never started here: the
      // generic failure. Anything else is the backend or the network.
      const refused = !verifier || err.status === UNAUTHORIZED
      this._say(providerErrorMessage(refused ? undefined : 'provider'), true)
      this._openProfile()
    }
  }

  /** Attach to the signed-in user who the provider said they are. */
  async _confirmLink(code) {
    this._openProfile()
    if (!this._session.token) return this._say(sessionErrorMessage(UNAUTHORIZED), true)
    try {
      const { provider } = await this._client.confirmLink(code)
      this._sayIdentity(linkedMessage(provider))
      this._renderIdentities()
    } catch (err) {
      this._sessionFailure(err)
    }
  }

  async _submit(creating) {
    if (this._busy) return
    const email = this._email.value.trim()
    const password = this._password.value
    const refusal = emailRefusal(email)
      || (creating ? passwordRefusal(password, AUTH_PASSWORD_MIN_CHARS) : null)
      || (password ? null : 'Vui lòng nhập mật khẩu')
    if (refusal) return this._say(refusal, true)
    if (creating) return this._askCode({ email, password })

    this._setBusy(true)
    this._say('Đang đăng nhập…')
    try {
      const session = await this._client.login(email, password)
      this._forget()
      this._session.signIn(session)
    } catch (err) {
      if (err.status !== SUPERSEDED) this._say(err.message, true)
    }
    this._setBusy(false)
  }

  /**
   * First step of a new account, and "Gửi lại mã": have a code sent to the email. Only
   * the address is sent. The password leaves the input for the memory of the page, where
   * it waits for the code and no longer than the code lasts.
   */
  async _askCode(account) {
    if (this._busy || !account) return
    this._setBusy(true)
    this._say('Đang gửi mã xác nhận tới email của bạn…')
    try {
      const sent = await this._client.register(account.email)
      this._password.value = ''
      this._codeSent.textContent = codeSentMessage(sent)
      this._say('')
      this._showStep('code')
      this._waiting = account
      this._expiry = setTimeout(() => {
        this._forget()
        this._say(codeExpiredMessage(), true)
      }, forgetDelayMs(sent))
    } catch (err) {
      if (err.status !== SUPERSEDED) this._say(err.message, true)
    }
    this._setBusy(false)
    if (this._waiting) this._code.focus()
  }

  /** Second step: give the code back; the account is created and the page signed in. */
  async _giveCode() {
    if (this._busy || !this._waiting) return
    const code = this._code.value.replace(/\s+/g, '')
    const refusal = codeRefusal(code)
    if (refusal) return this._say(refusal, true)

    this._setBusy(true)
    this._say('Đang xác nhận…')
    const { email, password } = this._waiting
    try {
      const session = await this._client.verify(email, code, password)
      this._forget()
      this._session.signIn(session)
    } catch (err) {
      if (err.status !== SUPERSEDED) this._say(err.message, true)
    }
    this._setBusy(false)
  }

  /**
   * Show the step of the form: 'code' while an account waits for its code. Any other
   * step drops what was waiting (the password with it) and the timer of its code.
   */
  _showStep(step) {
    const code = step === 'code'
    clearTimeout(this._expiry)
    this._expiry = null
    this._waiting = null
    this._code.value = ''
    this._credentials.hidden = code
    this._codeStep.hidden = !code
  }

  /**
   * Forget everything a person typed in the form and what waits for a code: the page
   * has another person in front of it (the session changed), or the code is over.
   */
  _forget() {
    this._email.value = ''
    this._password.value = ''
    this._codeSent.textContent = ''
    this._say('')
    this._showStep(null)
  }

  async _signOut() {
    // The page is a guest at once, before the backend answers; the session that is
    // ended there is the one of the token the page held, whoever signs in meanwhile.
    const token = this._session.token
    this._session.signOut()
    try {
      await this._client.logout(token)
    } catch {
      // The page forgot its token whatever the backend answers: nothing to say to a guest.
    }
  }

  async _link(provider) {
    try {
      window.location.assign(await this._client.linkUrl(provider))
    } catch (err) {
      this._sessionFailure(err)
    }
  }

  async _unlink(provider) {
    try {
      await this._client.unlink(provider)
      this._sayIdentity(`Đã gỡ tài khoản ${providerLabel(provider)}`)
    } catch (err) {
      this._sessionFailure(err)
    }
    this._renderIdentities()
  }

  /**
   * A route of the signed-in user failed. A session that is over is said in the main
   * message: the page is a guest by now and the linked accounts are hidden. An answer
   * that came for the person before is shown to nobody.
   */
  _sessionFailure(err) {
    if (err.status === SUPERSEDED) return
    if (err.status === UNAUTHORIZED) this._say(err.message, true)
    else this._sayIdentity(err.message, true)
  }

  _setBusy(busy) {
    this._busy = busy
    for (const control of [
      this._email, this._password, this._login, this._register,
      this._code, this._verify, this._resend, this._back,
    ]) control.disabled = busy
  }

  // ── Rendering ───────────────────────────────────────────────────────────

  _render() {
    const user = this._session.user
    this._mode.textContent = modeLabel(user)
    this._action.textContent = user ? 'Đăng xuất' : 'Đăng nhập'
    this._register.hidden = !this._offered.registration
    this._providers.replaceChildren(...this._offered.providers.map((provider) => {
      const start = button(`Đăng nhập với ${providerLabel(provider)}`, 'profile-button')
      start.addEventListener('click', () => this._start(provider))
      return start
    }))
    this._identities.hidden = !user || this._offered.providers.length === 0
    // Another person, or nobody: the accounts on screen, their buttons and what was
    // said about them belong to the person before.
    if ((user?.id ?? null) !== this._shownUserId) {
      this._shownUserId = user?.id ?? null
      this._identityList.replaceChildren()
      this._sayIdentity('')
    }
    if (user) this._renderIdentities()
  }

  /** The linked accounts, then one "Liên kết" per provider that is not linked yet. */
  async _renderIdentities() {
    if (!this._session.user || this._offered.providers.length === 0) return
    let linked
    try {
      linked = await this._client.identities()
    } catch (err) {
      // Rows that cannot be read again are not left on screen as if they were true.
      if (err.status !== SUPERSEDED) this._identityList.replaceChildren()
      return this._sessionFailure(err)
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
