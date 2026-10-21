// Wire types of the CodeLens backend API (backend/main.py).

export interface IndexRequest {
    repo_path: string;
    force_reindex?: boolean;
}

export interface QueryRequest {
    query: string;
    top_k?: number;
    explain?: boolean;
    repo_path?: string;
    /** Search-as-you-type: the last word is matched as a prefix. */
    live?: boolean;
}

export type MatchSource = 'both' | 'semantic' | 'keyword' | 'filter';

export interface ChunkResult {
    id: number;
    repo: string;
    file_path: string;
    symbol_name: string;
    kind: string;
    language: string;
    line_start: number;
    line_end: number;
    chunk_text: string;
    /** Cosine similarity in [0, 1]; null when no vector is available. */
    score: number | null;
    match: MatchSource;
}

export interface QueryFilters {
    in: string[];
    not_in: string[];
    lang: string[];
    kind: string[];
}

export interface QueryResponse {
    results: ChunkResult[];
    filters: QueryFilters;
    terms: string[];
    warnings: string[];
    explain_text: string | null;
    query_ms: number;
    total_indexed: number;
}

/** Explain code with the local LLM. Send `code` (no search), or `query` to explain the top results. */
export interface ExplainRequest {
    code?: string;
    file_path?: string;
    symbol_name?: string;
    language?: string;
    query?: string;
    repo_path?: string;
    top_k?: number;
}

export interface ExplainResponse {
    explain_text: string;
    model: string;
    ms: number;
}

/** Example searches built from the symbols indexed for a repo. */
export interface SuggestionsResponse {
    suggestions: string[];
    /** Short `lang:` names present in the repo, most common first. */
    languages: string[];
    kinds: string[];
}

export type IndexEvent =
    | { type: 'start'; repo: string }
    | { type: 'progress'; phase: 'scan' | 'embed'; done: number; total: number; file: string; chunks?: number }
    | { type: 'error'; message: string; file: string }
    | {
          type: 'complete';
          files: number;
          changed: number;
          unchanged: number;
          removed: number;
          total_chunks: number;
          embedded: number;
          embed_error: string | null;
          duration_ms: number;
      };

export interface StatusResponse {
    indexed_chunks: number;
    embedded_chunks: number;
    files: number;
    vector_backend: string;
    repo: string | null;
    last_indexed: string | null;
    embed_model: string;
    indexing: boolean;
    watching: boolean;
}

export interface HealthResponse {
    ollama: boolean;
    embed_model: string;
    embed_model_pulled: boolean;
    ollama_error: string | null;
    explain_model?: string;
    db: boolean;
    vector_backend: string;
}
