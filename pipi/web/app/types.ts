export type Element = {
  id: string;
  type: "text" | "shape" | "image" | "table" | "chart";
  x: number;
  y: number;
  w: number;
  h: number;
  text: string;
  font: string;
  font_size: number;
  line_height?: number;
  align?: "left" | "center" | "right" | "justify";
  valign?: "top" | "middle" | "bottom";
  color: string;
  fill: string;
  bold: boolean;
  editable: boolean;
  max_chars: number;
  asset_id: string | null;
  builtin_asset?: string | null;
  image_fit?: "contain" | "fill" | "cover";
  shape: "rect" | "ellipse" | "line";
  rows: string[][];
  labels: string[];
  values: number[];
};
export type ReasoningEffort = "auto" | "off" | "low" | "medium" | "high";
export type Slide = {
  id: string;
  name: string;
  background: string;
  elements: Element[];
  notes: string;
};
export type Deck = {
  id: string;
  title: string;
  outline: string[];
  slides: Slide[];
  version: number;
  updated: string;
};
export type Job = {
  id: string;
  deck_id: string | null;
  kind: string;
  status: string;
  cursor: number;
  error: string | null;
  result: { asset_id?: string; template_id?: string };
  updated: string;
};
export type Template = {
  id: string;
  name: string;
  confirmed: boolean;
  builtin: boolean;
  thumbnail?: string;
};
export type TemplateDetail = {
  name: string;
  layouts: Slide[];
  warnings: string[];
  previews?: string[];
  analysis?: { description?: string };
};
export type Profile = {
  profile: {
    id: number;
    display_name?: string;
    username: string;
    quota: number;
    quota_per_unit: number;
    stored_bytes: number;
    role: number;
  };
  models: { text: string[]; image: string[] };
  enabled: boolean;
  storage_limit: number;
  pricing_url?: string;
};
export type Policy = {
  enabled: boolean;
  text_models: string[];
  image_models: string[];
  generation_concurrency: number;
  export_concurrency: number;
  user_running: number;
  user_queued: number;
  upload_mb: number;
  storage_mb: number;
};
export type AdminConfig = {
  policy: Policy;
  jobs: Record<string, number>;
  oldest_queued_at: string | null;
  templates: { id: string; name: string; enabled: boolean }[];
};

class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

export async function api<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const response = await fetch("/api" + path, {
    ...options,
    headers: {
      ...(options.body && !(options.body instanceof FormData)
        ? { "Content-Type": "application/json" }
        : {}),
      ...options.headers,
    },
    cache: "no-store",
  });
  if (!response.ok) {
    const result = await response.json().catch(() => ({}));
    throw new ApiError(
      typeof result.detail === "string"
        ? result.detail
        : `request_failed_${response.status}`,
      response.status,
    );
  }
  return response.json();
}

export async function submitJob(path: string, body?: unknown): Promise<Job> {
  const payload = body === undefined ? undefined : JSON.stringify(body);
  const digest = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(path + "\n" + (payload || "")),
  );
  const fingerprint =
    "pipi-job-" +
    Array.from(new Uint8Array(digest), (b) =>
      b.toString(16).padStart(2, "0"),
    ).join("");
  // Retain only an opaque key after an ambiguous network failure, including reloads.
  // The same submission then resumes its existing job instead of charging again.
  const key = sessionStorage.getItem(fingerprint) || crypto.randomUUID();
  sessionStorage.setItem(fingerprint, key);
  try {
    const result = await api<Job>(path, {
      method: "POST",
      headers: { "Idempotency-Key": key },
      body: payload,
    });
    sessionStorage.removeItem(fingerprint);
    return result;
  } catch (error) {
    if (error instanceof ApiError && error.status >= 400 && error.status < 500)
      sessionStorage.removeItem(fingerprint);
    throw error;
  }
}
export function blankElement(type: Element["type"]): Element {
  return {
    id: crypto.randomUUID(),
    type,
    x: 100,
    y: 160,
    w: 600,
    h: 220,
    text: type === "text" ? "输入文字 / Your text" : "",
    font: "Noto Sans CJK SC",
    font_size: 32,
    color: "#172033",
    fill: "#6951CC",
    bold: false,
    editable: true,
    max_chars: 2000,
    asset_id: null,
    shape: "rect",
    rows:
      type === "table"
        ? [
            ["项目", "数值"],
            ["A", "100"],
            ["B", "200"],
          ]
        : [],
    labels: type === "chart" ? ["A", "B", "C"] : [],
    values: type === "chart" ? [20, 40, 30] : [],
  };
}
