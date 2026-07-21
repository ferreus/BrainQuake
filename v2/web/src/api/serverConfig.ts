// Persisted server base URL, mirroring v2/client's config.json + "Server
// Settings" dialog. localStorage stands in for that on-disk file.

const STORAGE_KEY = "brainquake.serverBaseUrl";

function defaultBaseUrl(): string {
  return import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000";
}

export function getBaseUrl(): string {
  return localStorage.getItem(STORAGE_KEY) ?? defaultBaseUrl();
}

export function setBaseUrl(url: string): void {
  const trimmed = url.trim().replace(/\/+$/, "");
  localStorage.setItem(STORAGE_KEY, trimmed);
}
