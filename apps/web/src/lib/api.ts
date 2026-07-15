import type {
  CreateDraftResponse,
  DocumentRecord,
  DraftDocType,
  DraftDocument,
  DraftSummary,
  GenerateArtifactsResponse,
  MatterArtifacts,
  MatterManifest,
  OcrStatus,
  QueryResponse,
} from "./types";

export const API_URL =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

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
  } catch {
    throw new ApiError(
      0,
      "Could not reach the backend. Is the API running on " + API_URL + "?",
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

export function generateArtifacts(
  id: string,
): Promise<GenerateArtifactsResponse> {
  return request<GenerateArtifactsResponse>(
    `/matters/${encodeURIComponent(id)}/artifacts`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
    },
  );
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

export function createDraft(
  id: string,
  docType: DraftDocType,
  instructions: string,
): Promise<CreateDraftResponse> {
  return request<CreateDraftResponse>(
    `/matters/${encodeURIComponent(id)}/drafts`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ doc_type: docType, instructions }),
    },
  );
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
