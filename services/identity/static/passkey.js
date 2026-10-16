/**
 * passkey.js
 * Client-side WebAuthn helpers wrapping @simplewebauthn/browser.
 */

async function registerPasskeyWithToken(token) {
    if (!window.SimpleWebAuthnBrowser) {
        throw new Error("SimpleWebAuthnBrowser library not loaded.");
    }

    // 1. Fetch registration options from server
    const optionsResp = await fetch("/passkeys/register/options", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ t: token }),
    });

    if (!optionsResp.ok) {
        const err = await optionsResp.json().catch(() => ({}));
        throw new Error(err.detail || "Failed to obtain passkey options.");
    }

    const optionsJSON = await optionsResp.json();

    // 2. Invoke browser biometric credential creation
    let attResp;
    try {
        attResp = await SimpleWebAuthnBrowser.startRegistration({ optionsJSON });
    } catch (error) {
        if (error.name === "InvalidStateError") {
            throw new Error("A passkey for this account already exists on this device.");
        }
        if (error.name === "NotAllowedError") {
            throw new Error("Biometric prompt was cancelled or timed out.");
        }
        throw error;
    }

    // 3. Send response back to server for verification
    const verifyResp = await fetch("/passkeys/register/verify", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ t: token, credential: attResp }),
    });

    if (!verifyResp.ok) {
        const err = await verifyResp.json().catch(() => ({}));
        throw new Error(err.detail || "Passkey verification failed on server.");
    }

    return await verifyResp.json();
}
