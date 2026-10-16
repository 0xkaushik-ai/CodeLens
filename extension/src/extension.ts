import * as vscode from 'vscode';
import * as fs from 'fs';
import * as path from 'path';
import { spawn, spawnSync, ChildProcess } from 'child_process';
import { CodeLensClient } from './client';
import { SearchPanelProvider } from './searchPanel';

let backendProcess: ChildProcess | undefined;
let statusBarItem: vscode.StatusBarItem;
let pollInterval: NodeJS.Timeout | undefined;
let output: vscode.OutputChannel;

const REQUIRED_MODULES = 'import fastapi, uvicorn, ollama, tree_sitter, numpy, watchdog, dotenv';

function config() {
    return vscode.workspace.getConfiguration('codelens');
}

function baseUrl(): string {
    return `http://127.0.0.1:${config().get<number>('port', 8000)}`;
}

export async function activate(context: vscode.ExtensionContext) {
    output = vscode.window.createOutputChannel('CodeLens');
    context.subscriptions.push(output);

    const client = new CodeLensClient(baseUrl);
    const provider = new SearchPanelProvider(context.extensionUri, client, output);
    context.subscriptions.push(
        vscode.window.registerWebviewViewProvider('codelens.sidebar', provider, {
            webviewOptions: { retainContextWhenHidden: true },
        }),
    );

    statusBarItem = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 100);
    statusBarItem.text = '$(sync~spin) CodeLens starting...';
    statusBarItem.command = 'codelens.search';
    statusBarItem.tooltip = 'Open CodeLens search';
    statusBarItem.show();
    context.subscriptions.push(statusBarItem);

    context.subscriptions.push(
        vscode.commands.registerCommand('codelens.search', () => provider.focus()),
        vscode.commands.registerCommand('codelens.reindex', () => provider.reindex(false)),
        vscode.commands.registerCommand('codelens.rebuildIndex', () => provider.reindex(true)),
        vscode.commands.registerCommand('codelens.showStatus', () => output.show()),
        vscode.commands.registerCommand('codelens.searchSelection', () => {
            const editor = vscode.window.activeTextEditor;
            const text = editor?.document.getText(editor.selection).trim();
            if (text) { provider.search(text.slice(0, 2000)); }
        }),
        vscode.commands.registerCommand('codelens.restartBackend', async () => {
            stopBackend();
            await ensureBackend(context, client);
            provider.refreshStatus();
        }),
    );

    // Don't block activation on the backend: the panel reports it as offline until it's up
    ensureBackend(context, client).finally(() => {
        provider.refreshStatus();
        pollStatus(client, provider);
        pollInterval = setInterval(() => pollStatus(client, provider), 15000);
    });
}

/** Directory containing `backend/main.py`: the setting, else the extension's own folder. */
function resolveBackendDir(context: vscode.ExtensionContext): string | undefined {
    const candidates = [config().get<string>('backendPath', ''), context.extensionPath].filter(Boolean);
    return candidates.find(dir => fs.existsSync(path.join(dir, 'backend', 'main.py')));
}

function pythonCandidates(backendDir: string): string[] {
    const configured = config().get<string>('pythonPath', '');
    const venv = process.platform === 'win32'
        ? path.join(backendDir, '.venv', 'Scripts', 'python.exe')
        : path.join(backendDir, '.venv', 'bin', 'python');
    return [configured, fs.existsSync(venv) ? venv : '', 'python3', 'python'].filter(Boolean);
}

function findPython(backendDir: string): string | undefined {
    return pythonCandidates(backendDir).find(
        p => spawnSync(p, ['-c', REQUIRED_MODULES], { cwd: backendDir, timeout: 20000 }).status === 0,
    );
}

async function ensureBackend(context: vscode.ExtensionContext, client: CodeLensClient): Promise<void> {
    if (await client.health()) {
        output.appendLine(`Using the CodeLens backend already running at ${baseUrl()}`);
        return;
    }
    if (!config().get<boolean>('autoStartBackend', true)) {
        output.appendLine('Backend not running and codelens.autoStartBackend is off.');
        return;
    }

    const backendDir = resolveBackendDir(context);
    if (!backendDir) {
        const choice = await vscode.window.showErrorMessage(
            'CodeLens: backend not found. Set "codelens.backendPath" to the folder that contains backend/main.py.',
            'Open Settings');
        if (choice) { vscode.commands.executeCommand('workbench.action.openSettings', 'codelens.backendPath'); }
        return;
    }

    const python = findPython(backendDir);
    if (!python) {
        const setup = `cd "${backendDir}" && python3 -m venv .venv && .venv/bin/pip install -e .`;
        output.appendLine(`No Python with the backend dependencies found. Set it up with:\n  ${setup}`);
        const choice = await vscode.window.showErrorMessage(
            'CodeLens: no Python environment with the backend dependencies. Create one, or set "codelens.pythonPath".',
            'Copy Setup Command', 'Open Settings');
        if (choice === 'Copy Setup Command') { vscode.env.clipboard.writeText(setup); }
        if (choice === 'Open Settings') {
            vscode.commands.executeCommand('workbench.action.openSettings', 'codelens.pythonPath');
        }
        return;
    }

    const port = String(config().get<number>('port', 8000));
    output.appendLine(`Starting backend: ${python} -m uvicorn backend.main:app --port ${port} (in ${backendDir})`);
    const startedAt = Date.now();
    backendProcess = spawn(python, ['-m', 'uvicorn', 'backend.main:app', '--host', '127.0.0.1', '--port', port], {
        cwd: backendDir,
        env: {
            ...process.env,
            // The Ollama desktop app keeps its CLI here on macOS
            PATH: `/Applications/Ollama.app/Contents/Resources${path.delimiter}${process.env.PATH ?? ''}`,
        },
    });
    backendProcess.stdout?.on('data', d => output.append(d.toString()));
    backendProcess.stderr?.on('data', d => output.append(d.toString()));
    backendProcess.on('exit', code => {
        output.appendLine(`Backend exited with code ${code}`);
        backendProcess = undefined;
        statusBarItem.text = '$(database) CodeLens: Offline';
        if (Date.now() - startedAt < 15000 && code !== 0 && code !== null) {
            vscode.window.showErrorMessage('CodeLens: the backend failed to start.', 'Show Logs')
                .then(choice => { if (choice) { output.show(); } });
        }
    });

    for (let i = 0; i < 40 && backendProcess; i++) {
        if (await client.health(1000)) { return; }
        await new Promise(r => setTimeout(r, 500));
    }
}

async function pollStatus(client: CodeLensClient, provider: SearchPanelProvider) {
    const health = await client.health();
    if (!health) {
        statusBarItem.text = '$(database) CodeLens: Offline';
        return;
    }
    try {
        const status = await client.status(provider.repoRoot());
        statusBarItem.text = status.indexing
            ? '$(sync~spin) CodeLens: indexing'
            : `$(database) CodeLens: ${status.indexed_chunks} chunks`;
        statusBarItem.tooltip = health.ollama
            ? `${status.files} files · ${status.embedded_chunks}/${status.indexed_chunks} embedded · ${status.vector_backend}`
            : 'Ollama is offline: keyword search only';
    } catch {
        statusBarItem.text = '$(database) CodeLens: Offline';
    }
}

function stopBackend() {
    if (backendProcess && !backendProcess.killed) {
        backendProcess.kill('SIGINT');
    }
    backendProcess = undefined;
}

export function deactivate() {
    if (pollInterval) { clearInterval(pollInterval); }
    stopBackend();
}
