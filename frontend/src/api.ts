export const apiUrl = (path: string) => `${(import.meta.env.VITE_API_BASE_URL || "").replace(/\/$/, "")}${path}`;

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(apiUrl(path), {
      ...init,
      headers: { "Content-Type": "application/json", ...init?.headers },
    });
  } catch {
    throw new Error(
      "Cannot reach the assistant. Start the backend and retry connection.",
    );
  }
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(
      body.error?.message ?? "The service is unavailable. Please retry.",
    );
  }
  return response.status === 204 ? (undefined as T) : response.json();
}

export async function streamChat(
  body: unknown,
  signal: AbortSignal,
  onEvent: (event: string, data: any) => void,
) {
  const response = await fetch(apiUrl("/api/chat/stream"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  if (!response.ok) {
    const result = await response.json().catch(() => ({}));
    throw new Error(
      result.error?.message ?? "Unable to start a response. Please retry.",
    );
  }
  if (!response.body)
    throw new Error("Streaming is not supported in this browser.");
  const reader = response.body.getReader(),
    decoder = new TextDecoder();
  let buffer = "",
    finished = false;
  try {
    while (true) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value, { stream: !done }).replace(/\r\n/g, "\n");
      let index: number;
      while ((index = buffer.indexOf("\n\n")) >= 0) {
        const frame = buffer.slice(0, index);
        buffer = buffer.slice(index + 2);
        const event = frame
          .split("\n")
          .find((l) => l.startsWith("event:"))
          ?.slice(6)
          .trim();
        const data = frame
          .split("\n")
          .filter((l) => l.startsWith("data:"))
          .map((l) => l.slice(5).trimStart())
          .join("\n");
        if (event && data) {
          onEvent(event, JSON.parse(data));
          if (event === "done" || event === "error") finished = true;
        }
      }
      if (done) break;
    }
    if (!finished)
      throw new Error(
        "The connection ended before the answer was saved. Please retry.",
      );
  } finally {
    reader.releaseLock();
  }
}
