/**
 * API client.
 *
 * Deliberately thin. The one thing it does beyond `fetch` is turn the backend's
 * structured 422 responses into readable messages: the backend explains
 * precisely why an input was rejected ("'Notacarbrand' was not recognised as a
 * vehicle make"), and discarding that in favour of a generic "Request failed"
 * would throw away the most useful part of the response.
 */

import type { Analysis, ManualVehicleInput, ReferenceData } from "./types";

const BASE_URL =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/api/v1";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly requestId?: string,
  ) {
    super(message);
    this.name = "ApiError";
  }

  /** Whether the caller can fix this by changing their input. */
  get isUserFixable(): boolean {
    return this.status >= 400 && this.status < 500;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${BASE_URL}${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...init?.headers },
    });
  } catch {
    throw new ApiError(
      "Could not reach the analysis service. Check that the API is running.",
      0,
    );
  }

  const requestId = response.headers.get("X-Request-ID") ?? undefined;

  if (!response.ok) {
    throw new ApiError(await readError(response), response.status, requestId);
  }

  return (await response.json()) as T;
}

async function readError(response: Response): Promise<string> {
  try {
    const body = await response.json();

    // FastAPI validation errors arrive as a list of per-field problems.
    if (Array.isArray(body?.detail)) {
      const problems = body.detail
        .map((item: { loc?: unknown[]; msg?: string }) => {
          const field = Array.isArray(item.loc)
            ? item.loc.filter((part) => part !== "body").join(".")
            : "";
          return field ? `${field}: ${item.msg}` : item.msg;
        })
        .filter(Boolean);
      if (problems.length) return problems.join("; ");
    }

    if (typeof body?.detail === "string") return body.detail;
    if (typeof body?.error === "string") {
      return body.detail ? `${body.error}: ${body.detail}` : body.error;
    }
  } catch {
    // fall through
  }

  if (response.status === 429) {
    return "Too many requests. Please wait a moment and try again.";
  }
  if (response.status === 501) {
    return "That input mode is not available yet.";
  }
  return `The request failed (HTTP ${response.status}).`;
}

export async function analyseManual(
  vehicle: ManualVehicleInput,
  language: "az" | "en" | "ru" = "az",
): Promise<Analysis> {
  return request<Analysis>("/analysis/manual", {
    method: "POST",
    body: JSON.stringify({
      vehicle: { ...vehicle, currency: "AZN" },
      language,
      include_narrative: true,
    }),
  });
}

export async function fetchReferenceData(): Promise<ReferenceData> {
  return request<ReferenceData>("/reference");
}
