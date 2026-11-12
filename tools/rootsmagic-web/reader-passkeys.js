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

  async function exchange(path, headers, body) {
    const response = await fetch(path, { method: "POST", headers: { Accept: "application/json", ...headers }, body });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "The passkey request could not be completed.");
    return data;
  }

  function showStatus(text) {
    status.textContent = text;
    status.hidden = !text;
  }

  async function complete(button, operation) {
    button.disabled = true;
    showStatus("Follow your device’s passkey prompt.");
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
    loginArea.hidden = false;
    loginButton.addEventListener("click", () => {
      void complete(loginButton, async () => {
        const form = document.querySelector('form[action="/login"]');
        const csrf = form.elements.csrf_token.value;
        const ceremony = await exchange("/login/passkey/options", { "X-CSRF-Token": csrf });
        const credential = await navigator.credentials.get({ publicKey: requestOptions(ceremony.options) });
        if (!credential) throw new Error("Choose a passkey to sign in.");
        const result = await exchange("/login/passkey/verify", {
          "Content-Type": "application/json",
          "X-CSRF-Token": csrf,
          "X-Passkey-Ceremony": ceremony.token,
        }, JSON.stringify({ credential: credentialJSON(credential), next: form.elements.next.value }));
        return result.next;
      });
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
