"use strict";

(() => {
  const loginArea = document.querySelector("#passkey-sign-in");
  const loginButton = document.querySelector("#passkey-login");
  const registerForm = document.querySelector("#passkey-register");
  const status = document.querySelector("#passkey-status");
  if (!window.PublicKeyCredential || !navigator.credentials || !window.isSecureContext) return;

  function decode(text) {
    return Uint8Array.from(atob(text.replaceAll("-", "+").replaceAll("_", "/")), value => value.charCodeAt(0));
  }

  function encode(value) {
    return btoa(String.fromCharCode(...new Uint8Array(value))).replaceAll("+", "-").replaceAll("/", "_").replace(/=+$/, "");
  }

  function creationOptions(options) {
    if (PublicKeyCredential.parseCreationOptionsFromJSON) return PublicKeyCredential.parseCreationOptionsFromJSON(options);
    return {
      ...options,
      challenge: decode(options.challenge),
      user: { ...options.user, id: decode(options.user.id) },
      excludeCredentials: options.excludeCredentials?.map(item => ({ ...item, id: decode(item.id) })),
    };
  }

  function requestOptions(options) {
    if (PublicKeyCredential.parseRequestOptionsFromJSON) return PublicKeyCredential.parseRequestOptionsFromJSON(options);
    return {
      ...options,
      challenge: decode(options.challenge),
      allowCredentials: options.allowCredentials?.map(item => ({ ...item, id: decode(item.id) })),
    };
  }

  function credentialJSON(credential) {
    if (credential.toJSON) return credential.toJSON();
    const response = {};
    for (const field of ["clientDataJSON", "attestationObject", "authenticatorData", "signature", "userHandle"]) {
      if (credential.response[field]) response[field] = encode(credential.response[field]);
    }
    return {
      id: credential.id,
      rawId: encode(credential.rawId),
      type: credential.type,
      response,
      clientExtensionResults: credential.getClientExtensionResults(),
    };
  }

  async function exchange(path, headers, body, signal) {
    const response = await fetch(path, { method: "POST", headers: { Accept: "application/json", ...headers }, body, signal });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "The passkey request could not be completed.");
    return data;
  }

  function showStatus(text) {
    status.textContent = text;
    status.hidden = !text;
  }

  async function complete(button, operation, message = "Follow your device’s passkey prompt.") {
    button.disabled = true;
    showStatus(message);
    try {
      const destination = await operation();
      window.location.assign(destination);
    } catch (error) {
      showStatus(error instanceof Error && error.name === "NotAllowedError"
        ? "The passkey request was cancelled or timed out. Try again when you’re ready."
        : error instanceof Error ? error.message : "The passkey request could not be completed.");
      button.disabled = false;
    }
  }

  if (loginArea && loginButton) {
    const form = document.querySelector('form[action="/login"]');
    const csrf = form.elements.csrf_token.value;
    const username = form.elements.username;
    let autofill;

    function loginOptions(signal) {
      return exchange("/login/passkey/options", { "X-CSRF-Token": csrf }, undefined, signal);
    }

    async function verifyLogin(credential, ceremony, signal) {
      const result = await exchange("/login/passkey/verify", {
        "Content-Type": "application/json",
        "X-CSRF-Token": csrf,
        "X-Passkey-Ceremony": ceremony.token,
      }, JSON.stringify({ credential: credentialJSON(credential), next: form.elements.next.value }), signal);
      return result.next;
    }

    async function startAutofill() {
      if (autofill || loginButton.disabled || document.hidden) return;
      const controller = new AbortController();
      const { signal } = controller;
      autofill = controller;
      let expiration;
      try {
        const ceremony = await loginOptions(signal);
        if (signal.aborted) return;
        const publicKey = requestOptions(ceremony.options);
        // Conditional requests can stay pending even after the server challenge expires.
        expiration = setTimeout(() => controller.abort(), Math.max(0, publicKey.timeout - 5000));
        const credential = await navigator.credentials.get({ publicKey, mediation: "conditional", signal });
        clearTimeout(expiration);
        if (credential && !signal.aborted) {
          await complete(loginButton, () => verifyLogin(credential, ceremony, signal), "Signing in…");
        }
      } catch {
        // An unavailable or cancelled AutoFill request leaves the sign-in form ready.
      } finally {
        clearTimeout(expiration);
        if (autofill === controller) autofill = undefined;
      }
    }

    loginArea.hidden = false;
    loginButton.addEventListener("click", () => {
      autofill?.abort();
      void complete(loginButton, async () => {
        const ceremony = await loginOptions();
        const credential = await navigator.credentials.get({ publicKey: requestOptions(ceremony.options) });
        if (!credential) throw new Error("Choose a passkey to sign in.");
        return verifyLogin(credential, ceremony);
      });
    });
    form.addEventListener("submit", () => autofill?.abort());
    window.addEventListener("pagehide", () => autofill?.abort());
    void PublicKeyCredential.isConditionalMediationAvailable?.().then((available) => {
      if (!available) return;
      username.addEventListener("focus", startAutofill);
      username.addEventListener("pointerdown", startAutofill);
      document.addEventListener("visibilitychange", startAutofill);
      window.addEventListener("pageshow", startAutofill);
      void startAutofill();
    }).catch(() => {
      // The explicit passkey button remains available.
    });
  }

  if (registerForm) {
    registerForm.hidden = false;
    document.querySelector("#passkey-unavailable").hidden = true;
    registerForm.addEventListener("submit", event => {
      event.preventDefault();
      const button = registerForm.querySelector('button[type="submit"]');
      const fields = new FormData(registerForm);
      void complete(button, async () => {
        const ceremony = await exchange("/account/passkeys/options", {}, new URLSearchParams(fields));
        registerForm.elements.password.value = "";
        if (registerForm.elements.code) registerForm.elements.code.value = "";
        const credential = await navigator.credentials.create({ publicKey: creationOptions(ceremony.options) });
        if (!credential) throw new Error("Create a passkey to finish setup.");
        const result = await exchange("/account/passkeys/verify", {
          "Content-Type": "application/json",
          "X-CSRF-Token": fields.get("csrf_token"),
          "X-Passkey-Ceremony": ceremony.token,
        }, JSON.stringify({ credential: credentialJSON(credential), name: fields.get("name") }));
        return result.next;
      });
    });
  }
})();
