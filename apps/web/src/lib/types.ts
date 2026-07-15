
export interface Citation {
  file: string;
  page: number;
  para: number | null;
}

export interface PageRecord {
  page: number;
  method: string;
  confidence: number;
  language: string;
  chars: number;
}

export type OcrStatus =
  | "not_needed"
  | "pending"
  | "running"
  | "done"
  | "failed";

export interface DocumentRecord {
  file: string;
  doc_type: string;
  pages: PageRecord[];
  needs_ocr_pages: number[];
  ocr_status: OcrStatus;
  ocr_error: string | null;
}

export interface MatterManifest {
  matter_id: string;
  title: string;
  created: string;
  documents: DocumentRecord[];
}

export interface ChronologyEntry {
  event_date: string | null;
  event: string;
  actor: string | null;
  cites: Citation[];
  confidence: "high" | "low_ocr";
}

export interface ProceedingEntry {
  order_date: string | null;
  court: string | null;
  direction: string;
  next_date: string | null;
  cites: Citation[];
}

export interface ContentionSide {
  position: string;
  cites: Citation[];
}

export interface ContentionEntry {
  issue: string;
  petitioner: ContentionSide | null;
  respondent: ContentionSide | null;
}

export interface IssueEntry {
  text: string;
  origin: "framed_by_court" | "inferred";
  cites: Citation[];
}

export interface DocIndexEntry {
  exhibit_no: string | null;
  title: string;
  doc_type: string;
  doc_date: string | null;
  pages: number;
  language: string;
  ocr_quality: string;
}

export interface ConflictPosition {
  position: string;
  cites: Citation[];
}

export interface ConflictEntry {
  fact: string;
  positions: ConflictPosition[];
}

export interface MatterArtifacts {
  matter_id: string;
  chronology: ChronologyEntry[];
  proceedings: ProceedingEntry[];
  contentions: ContentionEntry[];
  issues: IssueEntry[];
  doc_index: DocIndexEntry[];
  conflicts: ConflictEntry[];
  not_found: string[];
}

export interface Violation {
  kind: string;
  artifact: string;
  claim: string;
  cite: Citation | null;
}

export interface QueryResponse {
  answer: string;
  cites: Citation[];
  not_found: boolean;
}

export type DraftDocType =
  | "legal_notice"
  | "written_statement"
  | "bail_application"
  | "plaint";

export interface DraftParagraph {
  text: string;
  kind: "factual" | "boilerplate";
  cites: Citation[];
  verified: boolean;
}

export interface DraftDocument {
  matter_id: string;
  doc_type: DraftDocType;
  title: string;
  court_header: string | null;
  paragraphs: DraftParagraph[];
  prayer: string[];
  missing_info: string[];
}

export interface DraftSummary {
  draft_id: string;
  doc_type: DraftDocType;
  title: string;
  paragraphs: number;
  missing_info: number;
}

export interface DraftViolation {
  kind: string;
  paragraph: number;
  cite: Citation | null;
}

/* ── Jobs ─────────────────────────────────────────────────────────────
   Generation takes minutes, so the server does not hold the request open:
   POST queues a job and returns its id, and the result is written to the
   matter whether or not this browser is still listening. */

export type JobKind = "artifacts" | "draft";

export type JobStatus = "queued" | "running" | "succeeded" | "failed";

/** Which model wrote the output. Local and hosted are not interchangeable —
 *  a reader has to be told which one they are reading. */
export type JobProvider = "claude" | "ollama";

export interface ArtifactsJobResult {
  violations: Violation[];
}

export interface DraftJobResult {
  draft_id: string;
  violations: DraftViolation[];
}

interface JobRecordBase {
  job_id: string;
  matter_id: string;
  status: JobStatus;
  error: string | null;
  provider: JobProvider | null;
  created_at: string | null;
  started_at: string | null;
  finished_at: string | null;
}

export interface ArtifactsJobRecord extends JobRecordBase {
  kind: "artifacts";
  params: Record<string, never>;
  result: ArtifactsJobResult | null;
}

export interface DraftJobRecord extends JobRecordBase {
  kind: "draft";
  params: { doc_type?: DraftDocType; instructions?: string };
  result: DraftJobResult | null;
}

/** Discriminated on `kind` so `job.result` narrows to the right shape. */
export type JobRecord = ArtifactsJobRecord | DraftJobRecord;

/** What POST /artifacts and POST /drafts return — a receipt, not a result. */
export interface JobResponse {
  job_id: string;
  status: JobStatus;
}

/** Queued or running: work is in flight and worth polling for. */
export function isJobLive(job: JobRecord): boolean {
  return job.status === "queued" || job.status === "running";
}
