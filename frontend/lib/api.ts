export const BACKEND = process.env.NEXT_PUBLIC_BACKEND ?? "http://localhost:8000";

export async function readJson(response: Response) {
  const payload = await response.json().catch(() => ({}));
  if (!response.ok || payload.ok === false) {
    throw new Error(payload.detail || payload.error || `请求失败：HTTP ${response.status}`);
  }
  return payload;
}

export async function authSession() {
  return readJson(await fetch(`${BACKEND}/auth/session`, { credentials: "include" }));
}

export async function productFetch(path: string, init?: RequestInit) {
  const response = await fetch(`${BACKEND}${path}`, { credentials: "include", ...init });
  return readJson(response);
}

export async function createShareSnapshot(
  request: Record<string, any>,
  csrfToken: string,
  sources: Record<string, any>[] = [],
) {
  const token = csrfToken || (await authSession()).csrf_token || "";
  return productFetch("/share/snapshots", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(token ? { "x-otomo-csrf": token } : {}),
    },
    body: JSON.stringify({
      type: request.type,
      title: request.title,
      summary: request.summary || request.title || "",
      payload: request.payload || {},
      sources,
      spoiler_level: request.spoiler_level || "none",
      personalization_mode: request.personalization_mode || "public_generic",
      include_personalized_reason: request.personalization_mode === "public_personalized",
    }),
  });
}
