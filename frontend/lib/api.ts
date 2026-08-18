export const BACKEND = process.env.NEXT_PUBLIC_BACKEND ?? "http://localhost:8000";

export async function readJson(response: Response) {
  const payload = await response.json().catch(() => ({}));
  if (!response.ok || payload.ok === false) {
    const detail = payload.detail || payload.error;
    const message = typeof detail === "string"
      ? detail
      : detail?.message || (detail ? JSON.stringify(detail) : `请求失败：HTTP ${response.status}`);
    throw new Error(message);
  }
  return payload;
}

export async function authSession() {
  return readJson(await fetch(`${BACKEND}/auth/session`, { credentials: "include" }));
}

type ProductFetchOptions = {
  track?: boolean;
  label?: string;
  href?: string;
};

export async function productFetch(
  path: string,
  init?: RequestInit,
  options: ProductFetchOptions = {},
) {
  const taskId = options.track && typeof window !== "undefined" ? crypto.randomUUID() : "";
  if (taskId) window.dispatchEvent(new CustomEvent("otomo:task-start", {
    detail: { id: taskId, path, label: options.label, href: options.href },
  }));
  try {
    const response = await fetch(`${BACKEND}${path}`, { credentials: "include", ...init });
    const result = await readJson(response);
    if (taskId) window.dispatchEvent(new CustomEvent("otomo:task-finish", { detail: { id: taskId } }));
    return result;
  } catch (error) {
    if (taskId) window.dispatchEvent(new CustomEvent("otomo:task-finish", { detail: { id: taskId, error: String(error) } }));
    throw error;
  }
}

export async function createShareSnapshot(
  request: Record<string, any>,
  csrfToken: string,
  sources: Record<string, any>[] = [],
) {
  return productFetch("/share/snapshots", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(csrfToken ? { "x-otomo-csrf": csrfToken } : {}),
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
