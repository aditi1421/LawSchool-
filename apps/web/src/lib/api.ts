import type {
  DocumentRecord,
  DraftDocType,
  DraftDocument,
  DraftSummary,
  JobKind,
  JobRecord,
  JobResponse,
  MatterArtifacts,
  MatterManifest,
  OcrStatus,
  QueryResponse,
} from "./types";

export const API_URL =
  process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8010";

export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_URL}${path}`, init);
  } catch (err) {
    // fetch() rejects for several unrelated reasons — the server being down is
    // only one. It also rejects when the browser cannot read the File being
    // uploaded (moved, or not materialised locally by iCloud/Drive), when a
    // response carries no CORS headers, and when the request is aborted.
    // Reporting "backend unreachable" for all of them hides the actual cause
    // and sends everyone debugging the wrong thing, so pass it through.
    const cause = err instanceof Error ? `${err.name}: ${err.message}` : String(err);
    console.error(`[lawschool] fetch failed for ${path}`, err);
    throw new ApiError(
      0,
      `Request to ${API_URL}${path} failed — ${cause}. ` +
        `The API may be down, the browser may be unable to read the file ` +
        `(try copying it to your Desktop first), or the response may have been ` +
        `blocked. See the browser console for the original error.`,
    );
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      if (typeof body?.detail === "string") detail = body.detail;
    } catch {
      // keep statusText
    }
    throw new ApiError(res.status, detail || `Request failed (${res.status})`);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export function listMatters(): Promise<MatterManifest[]> {
  return request<MatterManifest[]>("/matters");
}

export function createMatter(title: string): Promise<MatterManifest> {
  return request<MatterManifest>("/matters", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title }),
  });
}

export function getMatter(id: string): Promise<MatterManifest> {
  return request<MatterManifest>(`/matters/${encodeURIComponent(id)}`);
}

export function deleteMatter(id: string): Promise<void> {
  return request<void>(`/matters/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
}

export function uploadFile(id: string, file: File): Promise<DocumentRecord> {
  const form = new FormData();
  form.append("file", file);
  return request<DocumentRecord>(
    `/matters/${encodeURIComponent(id)}/files`,
    { method: "POST", body: form },
  );
}

export function fileUrl(id: string, filename: string): string {
  return `${API_URL}/matters/${encodeURIComponent(id)}/files/${encodeURIComponent(filename)}`;
}

/** Queue (or retry) background OCR for one scanned document. */
export function startOcr(
  id: string,
  filename: string,
): Promise<{ ocr_status: OcrStatus }> {
  return request<{ ocr_status: OcrStatus }>(
    `/matters/${encodeURIComponent(id)}/files/${encodeURIComponent(filename)}/ocr`,
    { method: "POST" },
  );
}

/** One job record. 404 once the job is unknown to the server. */
export function getJob(jobId: string): Promise<JobRecord> {
  return request<JobRecord>(`/jobs/${encodeURIComponent(jobId)}`);
}

/** This matter's jobs, newest first. */
export function listJobs(id: string, kind?: JobKind): Promise<JobRecord[]> {
  const query = kind ? `?${new URLSearchParams({ kind })}` : "";
  return request<JobRecord[]>(
    `/matters/${encodeURIComponent(id)}/jobs${query}`,
  );
}

/** Queue brief generation. Returns a job id (202), not the brief — poll
 *  `getJob`. Throws ApiError 409 when a brief is already being generated for
 *  this matter, 422 when there is nothing readable to work from. */
export function generateArtifacts(id: string): Promise<JobResponse> {
  return request<JobResponse>(`/matters/${encodeURIComponent(id)}/artifacts`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
  });
}

export function getArtifacts(id: string): Promise<MatterArtifacts> {
  return request<MatterArtifacts>(
    `/matters/${encodeURIComponent(id)}/artifacts`,
  );
}

export function queryMatter(
  id: string,
  question: string,
): Promise<QueryResponse> {
  return request<QueryResponse>(`/matters/${encodeURIComponent(id)}/query`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question }),
  });
}

export function exportUrl(id: string): string {
  return `${API_URL}/matters/${encodeURIComponent(id)}/export.docx`;
}

/** Queue drafting. Returns a job id (202), not the draft — poll `getJob`,
 *  then fetch the draft named by `result.draft_id`. Same 409/422 as
 *  `generateArtifacts`. */
export function createDraft(
  id: string,
  docType: DraftDocType,
  instructions: string,
): Promise<JobResponse> {
  return request<JobResponse>(`/matters/${encodeURIComponent(id)}/drafts`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ doc_type: docType, instructions }),
  });
}

export function listDrafts(id: string): Promise<DraftSummary[]> {
  return request<DraftSummary[]>(`/matters/${encodeURIComponent(id)}/drafts`);
}

export function getDraft(id: string, draftId: string): Promise<DraftDocument> {
  return request<DraftDocument>(
    `/matters/${encodeURIComponent(id)}/drafts/${encodeURIComponent(draftId)}`,
  );
}

export function draftExportUrl(id: string, draftId: string): string {
  return `${API_URL}/matters/${encodeURIComponent(id)}/drafts/${encodeURIComponent(draftId)}.docx`;
}

export function getChunkText(
  id: string,
  file: string,
  page: number,
  para: number | null,
): Promise<{ text: string }> {
  const params = new URLSearchParams({ file, page: String(page) });
  if (para !== null && para !== undefined) params.set("para", String(para));
  return request<{ text: string }>(
    `/matters/${encodeURIComponent(id)}/chunk?${params}`,
  );
}
