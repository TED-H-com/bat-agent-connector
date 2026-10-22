import {nativeDesktop, browserAuthHeaders, browserSessionToken} from "./index.ts";
import {browserCredentialRefusals} from "../credential-refusals.js";

export interface ArtifactReference {artifact_id: string; revision: number; digest: string}
export const MAX_CONTENT_BYTES = 16 * 1024 * 1024;

// Browser-only, fixed same-origin read. No content_url, local path, redirect, or token URL.
export async function readArtifactContent(reference: ArtifactReference, size: number,
  token: string, signal: AbortSignal): Promise<Uint8Array<ArrayBuffer>> {
  if (nativeDesktop || !/^art_[0-9a-f]{32}$/.test(reference.artifact_id) ||
      !Number.isSafeInteger(reference.revision) || reference.revision < 1 ||
      !/^[0-9a-f]{64}$/.test(reference.digest) || !Number.isSafeInteger(size) || size < 0 || size > MAX_CONTENT_BYTES)
    throw new Error("Artifact content exceeds the supported bound or has an invalid reference");
  const response = await fetch(`/api/v1/artifacts/${reference.artifact_id}/revisions/${reference.revision}/content`, {
    method: "GET", headers: await browserAuthHeaders(token), redirect: "error", cache: "no-store",
    credentials: token === browserSessionToken ? "same-origin" : "omit", mode: "same-origin", signal: AbortSignal.any([signal, AbortSignal.timeout(30000)])
  });
  if (token !== browserSessionToken) await browserCredentialRefusals.refuse(token, response.status, 'GET', `/artifacts/${reference.artifact_id}/revisions/${reference.revision}/content`);
  if (response.status !== 200 || response.redirected ||
      response.headers.get("Content-Length") !== String(size) || !response.body) {
    await response.body?.cancel();
    throw new Error("Artifact content response does not match the exact revision");
  }
  const bytes = new Uint8Array(size), reader = response.body.getReader();
  let offset = 0;
  try {
    for (;;) {
      const part = await reader.read();
      if (part.done) break;
      if (offset + part.value.length > size) throw new Error("Artifact content exceeds its declared size");
      bytes.set(part.value, offset); offset += part.value.length;
    }
    if (offset !== size) throw new Error("Artifact content is incomplete");
  } catch (error) {await reader.cancel().catch(() => {}); throw error;}
  finally {reader.releaseLock();}
  const hash = [...new Uint8Array(await crypto.subtle.digest("SHA-256", bytes))]
    .map(value => value.toString(16).padStart(2, "0")).join("");
  signal.throwIfAborted();
  if (hash !== reference.digest) throw new Error("Artifact content digest does not match the exact revision");
  return bytes;
}
