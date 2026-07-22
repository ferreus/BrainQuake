import type { UploadFileType } from "./types";

// Same-origin, nginx-proxied path -- see v2/docker/nginx.conf. Works
// regardless of which host/IP the browser used to reach the web UI, unlike
// a baked-in or user-configurable absolute server URL.
const API_BASE = "/api";

export class ApiError extends Error {
  status: number;
  detail: unknown;

  constructor(status: number, detail: unknown) {
    const message =
      typeof detail === "string"
        ? detail
        : (detail as { detail?: string } | null)?.detail ?? `HTTP ${status}`;
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

async function parseErrorBody(res: Response): Promise<unknown> {
  const text = await res.text();
  try {
    return JSON.parse(text);
  } catch {
    return text || res.statusText;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      ...(init?.body ? { "Content-Type": "application/json" } : {}),
      ...init?.headers,
    },
  });
  if (!res.ok) {
    throw new ApiError(res.status, await parseErrorBody(res));
  }
  if (res.status === 204) {
    return undefined as T;
  }
  return (await res.json()) as T;
}

export function apiGet<T>(path: string): Promise<T> {
  return request<T>(path, { method: "GET" });
}

export function apiPost<T>(path: string, body?: unknown): Promise<T> {
  return request<T>(path, {
    method: "POST",
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
}

export function apiPut<T>(path: string, body?: unknown): Promise<T> {
  return request<T>(path, {
    method: "PUT",
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
}

export function apiDelete<T>(path: string): Promise<T> {
  return request<T>(path, { method: "DELETE" });
}

/** Plain-text GET, e.g. job logs (GET /jobs/{id}/log). */
export async function apiGetText(path: string): Promise<string> {
  const res = await fetch(`${API_BASE}${path}`);
  if (!res.ok) {
    throw new ApiError(res.status, await parseErrorBody(res));
  }
  return res.text();
}

/** Raw binary GET, e.g. surface mesh buffers (Phase 2+). */
export async function apiGetBinary(path: string): Promise<ArrayBuffer> {
  const res = await fetch(`${API_BASE}${path}`);
  if (!res.ok) {
    throw new ApiError(res.status, await parseErrorBody(res));
  }
  return res.arrayBuffer();
}

/**
 * Multipart upload with byte-level progress, mirroring api_client.py's
 * upload_file_with_progress. fetch() has no upload-progress event, so this
 * uses XMLHttpRequest directly.
 */
export function uploadFileWithProgress<T>(
  path: string,
  file: File,
  fileType: UploadFileType | null,
  onProgress?: (fraction: number) => void,
): { promise: Promise<T>; cancel: () => void } {
  const xhr = new XMLHttpRequest();
  const promise = new Promise<T>((resolve, reject) => {
    const form = new FormData();
    form.append("file", file);

    const query = fileType ? `?file_type=${fileType}` : "";
    xhr.open("POST", `${API_BASE}${path}${query}`);
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable && onProgress) {
        onProgress(e.loaded / e.total);
      }
    };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(JSON.parse(xhr.responseText) as T);
      } else {
        let detail: unknown = xhr.responseText;
        try {
          detail = JSON.parse(xhr.responseText);
        } catch {
          // leave as text
        }
        reject(new ApiError(xhr.status, detail));
      }
    };
    xhr.onerror = () => reject(new ApiError(0, "Network error during upload"));
    xhr.onabort = () => reject(new ApiError(0, "Upload cancelled"));
    xhr.send(form);
  });

  return { promise, cancel: () => xhr.abort() };
}
