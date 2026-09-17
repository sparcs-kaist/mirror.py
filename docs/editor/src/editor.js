import * as monaco from 'monaco-editor/editor/editor.api.js';
import 'monaco-editor/editor/browser/coreCommands.js';
import 'monaco-editor/editor/contrib/clipboard/browser/clipboard.js';
import 'monaco-editor/editor/contrib/find/browser/findController.js';
import 'monaco-editor/editor/contrib/folding/browser/folding.js';
import 'monaco-editor/editor/contrib/hover/browser/hoverContribution.js';
import 'monaco-editor/editor/contrib/bracketMatching/browser/bracketMatching.js';
import 'monaco-editor/editor/contrib/suggest/browser/suggestController.js';
import 'monaco-editor/editor/contrib/snippet/browser/snippetController2.js';
import 'monaco-editor/editor/contrib/contextmenu/browser/contextmenu.js';
import { jsonDefaults } from 'monaco-editor/languages/features/json/register.js';
import EditorWorker from 'monaco-editor/editor/editor.worker.js?worker';
import JsonWorker from 'monaco-editor/languages/features/json/json.worker.js?worker';
import { configSchema, createDefaultConfig, createPackage, validateConfig } from './schema.js';
import { renderForm } from './form.js';
import { editProperty, formatDocument, locateIssue, parseDocument, renamePackage } from './document.js';
import './editor.css';

self.MonacoEnvironment = {
  getWorker: (_id, label) => label === 'json' ? new JsonWorker() : new EditorWorker(),
};

const root = document.querySelector('#mirror-config-editor');
if (root) initializeEditor(root);

function initializeEditor(container) {
  document.body.classList.add('ce-page');
  container.innerHTML = `
    <div class="ce-toolbar" role="toolbar" aria-label="Configuration actions">
      <button type="button" id="ce-new">New configuration</button>
      <button type="button" id="ce-open">Open file</button>
      <button type="button" id="ce-format">Format JSON</button>
      <button type="button" id="ce-copy">Copy JSON</button>
      <button type="button" id="ce-download" class="ce-primary" disabled>Download config.json</button>
      <input type="file" id="ce-file-input" accept=".json,application/json" hidden>
    </div>
    <div class="ce-statusbar"><span id="ce-status" role="status" aria-live="polite">Loading editor…</span><span id="ce-modified"></span></div>
    <p class="ce-privacy">Files stay in this browser tab. Nothing is uploaded or saved automatically. Passwords remain visible in the JSON.</p>
    <div class="ce-tabs" role="tablist" aria-label="Editor pane">
      <button type="button" role="tab" aria-selected="true" aria-controls="ce-json-pane" id="ce-json-tab">JSON</button>
      <button type="button" role="tab" aria-selected="false" aria-controls="ce-settings-pane" id="ce-settings-tab">Settings</button>
    </div>
    <div class="ce-workspace" data-pane="json">
      <section id="ce-json-pane" class="ce-pane" aria-label="JSON editor"><h2>config.json</h2><div id="ce-monaco"></div></section>
      <div id="ce-divider" role="separator" aria-label="Resize editor panes" aria-orientation="vertical" aria-valuemin="25" aria-valuemax="75" aria-valuenow="50" tabindex="0"></div>
      <section id="ce-settings-pane" class="ce-pane" aria-label="Configuration settings"><h2>Settings</h2><fieldset id="ce-form"></fieldset></section>
    </div>
    <div id="ce-notice" role="status" aria-live="polite"></div>
    <details class="ce-diagnostics"><summary id="ce-issues-heading">Configuration checks</summary><ul id="ce-issues"></ul></details>
  `;
  const find = (id) => container.querySelector(`#${id}`);
  const initial = JSON.stringify(createDefaultConfig(), null, 2) + '\n';
  const model = monaco.editor.createModel(initial, 'json', monaco.Uri.parse('inmemory://mirror/config.json'));
  jsonDefaults.setDiagnosticsOptions({
    validate: true, allowComments: false, trailingCommas: 'error', enableSchemaRequest: false,
    schemaRequest: 'ignore', schemaValidation: 'error',
    schemas: [{ uri: 'inmemory://mirror/config-schema.json', fileMatch: [model.uri.toString()], schema: configSchema }],
  });
  const editor = monaco.editor.create(find('ce-monaco'), {
    model, automaticLayout: true, minimap: { enabled: false }, fontSize: 14,
    scrollBeyondLastLine: false, tabSize: 2, insertSpaces: true, folding: true,
    wordWrap: 'on', ariaLabel: 'Configuration JSON', theme: 'vs',
    fixedOverflowWidgets: true, editContext: false,
  });
  let baseline = initial;
  let timer;
  let selectedPackage;
  let importVersion = 0;
  let current;
  let issues = [];

  function notice(message) { find('ce-notice').textContent = message; }

  function apply(edits) {
    if (!edits.length) return;
    editor.pushUndoStop();
    editor.executeEdits('configuration-form', edits.map((edit) => {
      const start = model.getPositionAt(edit.offset);
      const end = model.getPositionAt(edit.offset + edit.length);
      return { range: new monaco.Range(start.lineNumber, start.column, end.lineNumber, end.column), text: edit.content, forceMoveMarkers: true };
    }));
    editor.pushUndoStop();
    checkDocument();
  }

  function mutate(callback) {
    try { callback(); notice(''); } catch (error) { notice(error.message); }
  }

  function render(value) {
    const active = document.activeElement;
    const focusPath = active?.dataset?.path;
    const selection = active?.tagName === 'INPUT' && active.type === 'text'
      ? [active.selectionStart, active.selectionEnd] : null;
    const scroll = find('ce-form').scrollTop;
    const openGroups = new Set([...find('ce-form').querySelectorAll('details[open]')].map((item) => item.dataset.path || item.querySelector('summary')?.textContent));
    renderForm(find('ce-form'), value, {
      selectedPackage,
      onSelectPackage(id) { selectedPackage = id; render(value); },
      onChange(path, newValue) { mutate(() => apply(editProperty(model.getValue(), path, newValue))); },
      onRemove(path) { mutate(() => apply(editProperty(model.getValue(), path, undefined))); },
      onRename(oldId, newId) {
        mutate(() => {
          const edits = renamePackage(model.getValue(), oldId, newId);
          selectedPackage = newId;
          apply(edits);
        });
      },
      onAddPackage(id, method) {
        mutate(() => {
          if (!id.trim()) throw new Error('Enter a package ID.');
          const parsed = parseDocument(model.getValue());
          if (!parsed.valid || !parsed.value || typeof parsed.value !== 'object') throw new Error('Repair JSON before adding a package.');
          if (Object.hasOwn(parsed.value.packages || {}, id)) throw new Error(`Package ID already exists: ${id}`);
          selectedPackage = id;
          apply(editProperty(model.getValue(), ['packages', id], createPackage(id, method)));
        });
      },
      onDeletePackage(id) {
        if (window.confirm(`Delete package "${id}"?`)) mutate(() => {
          selectedPackage = undefined;
          apply(editProperty(model.getValue(), ['packages', id], undefined));
        });
      },
    });
    for (const item of find('ce-form').querySelectorAll('details')) {
      if (openGroups.has(item.dataset.path || item.querySelector('summary')?.textContent)) item.open = true;
    }
    find('ce-form').scrollTop = scroll;
    if (focusPath) {
      const field = [...find('ce-form').querySelectorAll('[data-path]')].find((item) => item.dataset.path === focusPath && /^(INPUT|SELECT|TEXTAREA)$/.test(item.tagName));
      field?.focus({ preventScroll: true });
      if (selection && field?.type === 'text') field.setSelectionRange(...selection);
    }
  }

  function checkDocument() {
    clearTimeout(timer);
    const text = model.getValue();
    current = parseDocument(text);
    issues = current.valid ? validateConfig(current.value) : current.issues;
    const errors = issues.filter((item) => item.severity === 'error');
    const warnings = issues.length - errors.length;
    find('ce-status').textContent = errors.length ? `${errors.length} error${errors.length === 1 ? '' : 's'} — repair before downloading`
      : warnings ? `Valid configuration — ${warnings} warning${warnings === 1 ? '' : 's'}` : 'Valid configuration';
    find('ce-status').dataset.state = errors.length ? 'error' : 'valid';
    find('ce-download').disabled = errors.length > 0;
    find('ce-format').disabled = !current.valid;
    find('ce-modified').textContent = text === baseline ? '' : 'Modified';
    const renderable = current.valid && current.value !== null && typeof current.value === 'object' && !Array.isArray(current.value);
    find('ce-form').disabled = !renderable;
    if (renderable) render(current.value);
    const list = find('ce-issues');
    list.replaceChildren();
    find('ce-issues-heading').textContent = `Configuration checks (${errors.length} errors, ${warnings} warnings)`;
    const markers = [];
    for (const issue of issues) {
      const position = issue.offset === undefined ? locateIssue(current.tree, issue.path || []) : issue;
      const start = model.getPositionAt(position.offset);
      const end = model.getPositionAt(position.offset + Math.max(position.length, 1));
      markers.push({ severity: issue.severity === 'error' ? monaco.MarkerSeverity.Error : monaco.MarkerSeverity.Warning,
        startLineNumber: start.lineNumber, startColumn: start.column, endLineNumber: end.lineNumber, endColumn: end.column, message: issue.message });
      const item = document.createElement('li');
      const button = document.createElement('button');
      button.type = 'button';
      button.textContent = `${issue.severity}: ${(issue.path || []).join('.') || '$'} — ${issue.message}`;
      button.addEventListener('click', () => {
        selectPane('json'); editor.revealPositionInCenter(start); editor.setPosition(start); editor.focus();
      });
      item.append(button); list.append(item);
    }
    monaco.editor.setModelMarkers(model, 'mirror-config', markers);
  }

  model.onDidChangeContent(() => {
    clearTimeout(timer);
    find('ce-status').textContent = 'Checking configuration…';
    find('ce-download').disabled = true;
    find('ce-form').disabled = true;
    timer = setTimeout(checkDocument, 250);
  });

  function replaceDocument(text) {
    if (model.getValue() !== baseline && !window.confirm('Replace the current configuration? Unsaved changes will be lost.')) return;
    baseline = text;
    selectedPackage = undefined;
    model.setValue(text);
    checkDocument();
    notice('');
  }

  find('ce-new').addEventListener('click', () => {
    importVersion++;
    replaceDocument(JSON.stringify(createDefaultConfig(), null, 2) + '\n');
  });
  find('ce-open').addEventListener('click', () => find('ce-file-input').click());
  find('ce-file-input').addEventListener('change', async (event) => {
    const file = event.target.files[0];
    if (!file) return;
    const version = ++importVersion;
    try {
      const text = await file.text();
      if (version === importVersion) replaceDocument(text);
    } catch { notice('The file could not be read. Your current configuration has been kept.'); }
    event.target.value = '';
  });
  find('ce-format').addEventListener('click', () => mutate(() => apply(formatDocument(model.getValue()))));
  find('ce-copy').addEventListener('click', async () => {
    try { await navigator.clipboard.writeText(model.getValue()); notice('JSON copied.'); }
    catch { editor.focus(); editor.setSelection(model.getFullModelRange()); notice('Clipboard access is unavailable. Copy the selected JSON with your keyboard.'); }
  });
  find('ce-download').addEventListener('click', () => {
    checkDocument();
    if (issues.some((item) => item.severity === 'error')) return;
    const text = model.getValue();
    const url = URL.createObjectURL(new Blob([text], { type: 'application/json' }));
    const link = document.createElement('a');
    link.href = url; link.download = 'config.json'; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    baseline = text; find('ce-modified').textContent = ''; notice('Configuration downloaded.');
  });

  function selectPane(name) {
    container.querySelector('.ce-workspace').dataset.pane = name;
    find('ce-json-tab').setAttribute('aria-selected', String(name === 'json'));
    find('ce-settings-tab').setAttribute('aria-selected', String(name === 'settings'));
    editor.layout();
  }
  find('ce-json-tab').addEventListener('click', () => selectPane('json'));
  find('ce-settings-tab').addEventListener('click', () => selectPane('settings'));
  const divider = find('ce-divider');
  function resize(percent) {
    const clamped = Math.max(25, Math.min(75, percent));
    container.querySelector('.ce-workspace').style.setProperty('--ce-json-width', `${clamped}%`);
    divider.setAttribute('aria-valuenow', String(Math.round(clamped))); editor.layout();
  }
  divider.addEventListener('pointerdown', (event) => { divider.setPointerCapture(event.pointerId); event.preventDefault(); });
  divider.addEventListener('pointermove', (event) => {
    if (!divider.hasPointerCapture(event.pointerId)) return;
    const bounds = container.querySelector('.ce-workspace').getBoundingClientRect();
    resize((event.clientX - bounds.left) / bounds.width * 100);
  });
  divider.addEventListener('pointerup', (event) => divider.releasePointerCapture(event.pointerId));
  divider.addEventListener('keydown', (event) => {
    if (!['ArrowLeft', 'ArrowRight'].includes(event.key)) return;
    event.preventDefault(); resize(Number(divider.getAttribute('aria-valuenow')) + (event.key === 'ArrowLeft' ? -5 : 5));
  });
  window.addEventListener('beforeunload', (event) => {
    if (model.getValue() !== baseline) { event.preventDefault(); event.returnValue = ''; }
  });
  checkDocument();
}
