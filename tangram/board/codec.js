export function bytesToBase64Url(bytes) {
    let binary = "";
    for (const byte of bytes)
        binary += String.fromCharCode(byte);
    return btoa(binary).replace(/=/g, "").replace(/\+/g, "-").replace(/\//g, "_");
}
export function base64UrlToBytes(value) {
    if (!/^[A-Za-z0-9_-]*$/.test(value))
        throw new Error("invalid base64url");
    const padded = value.replace(/-/g, "+").replace(/_/g, "/")
        .padEnd(value.length + (4 - value.length % 4) % 4, "=");
    try {
        const binary = atob(padded);
        return Uint8Array.from(binary, (char) => char.charCodeAt(0));
    }
    catch {
        throw new Error("invalid base64url");
    }
}
export function utf8Bytes(value) {
    return new TextEncoder().encode(value).byteLength;
}
