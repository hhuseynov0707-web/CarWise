/**
 * API client.
 *
 * Deliberately thin. The one thing it does beyond `fetch` is turn the backend's
 * structured 422 responses into readable messages: the backend explains
 * precisely why an input was rejected ("'Notacarbrand' was not recognised as a
 * vehicle make"), and discarding that in favour of a generic "Request failed"
 * would throw away the most useful part of the response.
 */

import type {
  Analysis,
  ManualVehicleInput,
  ProfileUpdate,
  ReferenceData,
  DiscoverResponse,
  FindsResponse,
  Registration,
  SavedVehicle,
  SaveVehiclePayload,
  User,
} from "./types";

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
      // The session lives in an HttpOnly cookie, which the browser only
      // attaches when asked to: the app and the API are different origins.
      credentials: "include",
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

  // 204 carries no body, and asking for JSON there throws.
  if (response.status === 204) return undefined as T;

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


// --- accounts --------------------------------------------------------------
//
// Each of these sets or clears the session cookie as a side effect; none of
// them returns the token, because the page is not allowed to see it.

export async function register(payload: Registration): Promise<User> {
  return request<User>("/auth/register", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function signIn(email: string, password: string): Promise<User> {
  return request<User>("/auth/login", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });
}

export async function signOut(): Promise<void> {
  await request<void>("/auth/logout", { method: "POST" });
}

/**
 * The signed-in user, or null.
 *
 * A 401 here is the ordinary answer for a visitor, not a failure, so it is
 * translated rather than thrown — every caller would otherwise have to catch
 * it to render a sign-in form.
 */
export async function fetchMe(): Promise<User | null> {
  try {
    return await request<User>("/auth/me");
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) return null;
    throw error;
  }
}

export async function updateProfile(payload: ProfileUpdate): Promise<User> {
  return request<User>("/auth/me", {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}


// --- saved vehicles --------------------------------------------------------

export async function listSaved(): Promise<SavedVehicle[]> {
  return request<SavedVehicle[]>("/saved");
}

export async function saveVehicle(payload: SaveVehiclePayload): Promise<SavedVehicle> {
  return request<SavedVehicle>("/saved", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function removeSaved(id: number): Promise<void> {
  await request<void>(`/saved/${id}`, { method: "DELETE" });
}


// --- today's finds ---------------------------------------------------------

export async function fetchFinds(limit = 15): Promise<FindsResponse> {
  return request<FindsResponse>(`/finds?limit=${limit}`);
}


// --- discover --------------------------------------------------------------

export async function fetchDiscover(range?: {
  low: number;
  high: number;
}): Promise<DiscoverResponse> {
  const query = range ? `?budget_low=${range.low}&budget_high=${range.high}` : "";
  return request<DiscoverResponse>(`/discover${query}`);
}
