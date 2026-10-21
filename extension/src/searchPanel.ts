import * as vscode from 'vscode';
import * as fs from 'fs';
import * as path from 'path';
import * as crypto from 'crypto';
import { CodeLensClient } from './client';
import type { ExplainRequest } from '../../shared/types';

type WebviewMessage =
    | { type: 'ready' }
    | { type: 'query'; text: string; live: boolean; seq: number }
    | { type: 'jumpTo'; file: string; line: number; lineEnd: number }
    | { type: 'copy'; text: string }
    | { type: 'reindex'; force: boolean }
    | { type: 'explainSnippet'; reqId: string; code: string; file: string; symbol: string; language: string; query?: string }
    | { type: 'explainResults'; reqId: string; query: string }
    | { type: 'openExternal'; url: string };

/** Links the webview may open: setup docs only. */
const EXTERNAL_LINKS = new Set([
    'https://ollama.com/download',
    'https://github.com/TryingtobeingNikhil/CodeLens#-ollama-optional',
]);

interface PendingExplain { reqId: string; title: string; body: ExplainRequest }

export class SearchPanelProvider implements vscode.WebviewViewProvider {
    private view?: vscode.WebviewView;
    private inFlight?: AbortController;
    private indexing = false;
    private pendingSearch?: string;
    private pendingExplain?: PendingExplain;
    private explainSeq = 0;
    private lastSuggestKey = '';
    private readonly flash = vscode.window.createTextEditorDecorationType({
        backgroundColor: new vscode.ThemeColor('editor.findMatchHighlightBackground'),
        isWholeLine: true,
    });

    constructor(
        private readonly extensionUri: vscode.Uri,
        private readonly client: CodeLensClient,
        private readonly output: vscode.OutputChannel,
    ) {}

    resolveWebviewView(webviewView: vscode.WebviewView) {
        this.view = webviewView;
        webviewView.webview.options = {
            enableScripts: true,
            localResourceRoots: [vscode.Uri.joinPath(this.extensionUri, 'extension', 'media')],
        };
        webviewView.webview.html = this.getHtml(webviewView.webview);
        webviewView.webview.onDidReceiveMessage((msg: WebviewMessage) => this.onMessage(msg));
    }

    /** Workspace folder of the active editor, else the first folder. */
    repoRoot(): string | undefined {
        const uri = vscode.window.activeTextEditor?.document.uri;
        const folder = (uri && vscode.workspace.getWorkspaceFolder(uri)) ?? vscode.workspace.workspaceFolders?.[0];
        return folder?.uri.fsPath;
    }

    focus() {
        vscode.commands.executeCommand('codelens.sidebar.focus').then(() => this.post({ type: 'focus' }));
    }

    /** Run a search from outside the panel (e.g. "find similar code"). */
    search(text: string) {
        this.pendingSearch = text;
        this.focus();
        if (this.view) {
            this.post({ type: 'setQuery', text });
            this.pendingSearch = undefined;
        }
    }

    /**
     * Explain code without searching ("CodeLens: Explain Selection"). The answer appears
     * at the top of the sidebar.
     */
    explainSelection(code: string, file: string, language: string) {
        this.explainSeq += 1;
        const pending: PendingExplain = {
            reqId: `sel-${this.explainSeq}`,
            title: file ? `Selection in ${file}` : 'Selection',
            body: { code, file_path: file, language },
        };
        this.focus();
        if (this.view) {
            void this.runExplain(pending.reqId, pending.body, pending.title);
        } else {
            this.pendingExplain = pending;
        }
    }

    private post(message: unknown) {
        this.view?.webview.postMessage(message);
    }

    private async onMessage(msg: WebviewMessage) {
        switch (msg.type) {
            case 'ready':
                this.lastSuggestKey = '';   // a reloaded webview needs them again
                await this.refreshStatus();
                if (this.pendingSearch) {
                    this.post({ type: 'setQuery', text: this.pendingSearch });
                    this.pendingSearch = undefined;
                }
                if (this.pendingExplain) {
                    const p = this.pendingExplain;
                    this.pendingExplain = undefined;
                    void this.runExplain(p.reqId, p.body, p.title);
                }
                break;
            case 'query':
                await this.runQuery(msg.text, msg.live, msg.seq);
                break;
            case 'explainSnippet':
                await this.runExplain(msg.reqId, {
                    code: msg.code, file_path: msg.file, symbol_name: msg.symbol,
                    language: msg.language, query: msg.query,
                });
                break;
            case 'explainResults':
                await this.runExplain(msg.reqId, { query: msg.query, repo_path: this.repoRoot(), top_k: 5 },
                    `Top results for "${msg.query}"`);
                break;
            case 'openExternal':
                if (EXTERNAL_LINKS.has(msg.url)) { await vscode.env.openExternal(vscode.Uri.parse(msg.url)); }
                break;
            case 'jumpTo':
                await this.jumpTo(msg.file, msg.line, msg.lineEnd);
                break;
            case 'copy':
                await vscode.env.clipboard.writeText(msg.text);
                vscode.window.setStatusBarMessage('CodeLens: copied', 1500);
                break;
            case 'reindex':
                await this.reindex(msg.force);
                break;
        }
    }

    /**
     * Run an explanation and route the answer back to whoever asked (a result card,
     * or the box at the top when `title` is given).
     */
    private async runExplain(reqId: string, body: ExplainRequest, title?: string) {
        this.post({ type: 'explainStart', reqId, title });
        try {
            const res = await this.client.explain(body);
            this.post({ type: 'explainResult', reqId, title, text: res.explain_text, model: res.model, ms: res.ms });
        } catch (e: any) {
            this.post({ type: 'explainError', reqId, title, message: describeError(e) });
        }
    }

    private async runQuery(text: string, live: boolean, seq: number) {
        // A newer keystroke supersedes the request in flight
        this.inFlight?.abort();
        const controller = new AbortController();
        this.inFlight = controller;
        const repo = this.repoRoot();
        try {
            const res = await this.client.query(
                { query: text, top_k: 10, live, repo_path: repo }, controller.signal);
            this.post({ type: 'results', seq, repo, ...res });
        } catch (e: any) {
            if (controller.signal.aborted) { return; }
            this.post({ type: 'error', seq, message: describeError(e) });
        } finally {
            if (this.inFlight === controller) { this.inFlight = undefined; }
        }
    }

    async reindex(force: boolean) {
        const repo = this.repoRoot();
        if (!repo) {
            this.post({ type: 'error', message: 'Open a folder to index it.' });
            return;
        }
        if (this.indexing) { return; }
        this.indexing = true;
        this.focus();
        this.post({ type: 'indexStart', force });
        try {
            for await (const ev of this.client.index(repo, force)) {
                if (ev.type === 'progress') {
                    this.post({ type: 'indexProgress', phase: ev.phase, done: ev.done, total: ev.total, file: ev.file });
                } else if (ev.type === 'error') {
                    this.output.appendLine(`Index error in ${ev.file}: ${ev.message}`);
                } else if (ev.type === 'complete') {
                    this.output.appendLine(
                        `Indexed ${repo}: ${ev.files} files (${ev.changed} changed, ${ev.removed} removed), ` +
                        `${ev.total_chunks} chunks, ${ev.embedded} embedded in ${ev.duration_ms}ms`);
                    this.post({ ...ev, type: 'indexDone' });
                    if (ev.embed_error) {
                        vscode.window.showWarningMessage(
                            'CodeLens indexed your files, but embeddings failed. Keyword search works; ' +
                            'semantic search needs Ollama. Re-index once it is running.', 'Show Logs')
                            .then(c => { if (c) { this.output.show(); } });
                    }
                }
            }
        } catch (e: any) {
            this.post({ type: 'error', message: describeError(e) });
        } finally {
            this.indexing = false;
            await this.refreshStatus();
        }
    }

    async refreshStatus() {
        const health = await this.client.health();
        if (!health) {
            this.post({ type: 'status', online: false });
            return;
        }
        try {
            const status = await this.client.status(this.repoRoot());
            this.post({
                type: 'status', online: true, ollama: health.ollama, modelPulled: health.embed_model_pulled,
                embedModel: health.embed_model, explainModel: health.explain_model, ...status,
            });
            await this.refreshSuggestions(status.indexed_chunks);
        } catch (e: any) {
            this.post({ type: 'status', online: false, message: describeError(e) });
        }
    }

    /** Example searches from this repo's own symbols; refetched when the index changes. */
    private async refreshSuggestions(chunks: number) {
        const repo = this.repoRoot();
        const key = `${repo}:${chunks}`;
        if (key === this.lastSuggestKey) { return; }
        try {
            const res = chunks > 0 ? await this.client.suggestions(repo)
                : { suggestions: [], languages: [], kinds: [] };
            this.lastSuggestKey = key;
            this.post({ type: 'suggestions', ...res });
        } catch {
            // Older backend without /suggestions: the panel just shows no examples
        }
    }

    private async jumpTo(file: string, line: number, lineEnd: number) {
        const root = this.repoRoot();
        if (!root) { return; }
        const absolute = path.resolve(root, file);
        if (path.relative(root, absolute).startsWith('..')) { return; }  // stay inside the workspace
        try {
            const doc = await vscode.workspace.openTextDocument(vscode.Uri.file(absolute));
            const editor = await vscode.window.showTextDocument(doc);
            const start = Math.max(0, Math.min(line - 1, doc.lineCount - 1));
            const end = Math.max(start, Math.min(lineEnd - 1, doc.lineCount - 1));
            const pos = new vscode.Position(start, doc.lineAt(start).firstNonWhitespaceCharacterIndex);
            editor.selection = new vscode.Selection(pos, pos);
            editor.revealRange(new vscode.Range(start, 0, end, 0), vscode.TextEditorRevealType.InCenterIfOutsideViewport);
            editor.setDecorations(this.flash, [new vscode.Range(start, 0, end, 0)]);
            setTimeout(() => editor.setDecorations(this.flash, []), 1200);
        } catch (e: any) {
            vscode.window.showErrorMessage(`CodeLens: cannot open ${file}: ${e.message}`);
        }
    }

    private getHtml(webview: vscode.Webview): string {
        const htmlPath = vscode.Uri.joinPath(this.extensionUri, 'extension', 'media', 'panel.html');
        const nonce = crypto.randomBytes(16).toString('base64');
        return fs.readFileSync(htmlPath.fsPath, 'utf-8')
            .replace(/\{\{cspSource\}\}/g, webview.cspSource)
            .replace(/\{\{nonce\}\}/g, nonce);
    }
}

function describeError(e: any): string {
    if (e?.cause?.code === 'ECONNREFUSED' || e?.message === 'fetch failed') {
        return 'The CodeLens backend is not running. Run "CodeLens: Restart Backend" or see the CodeLens output.';
    }
    if (e?.name === 'TimeoutError') { return 'The backend took too long to answer.'; }
    return e?.message ?? String(e);
}
