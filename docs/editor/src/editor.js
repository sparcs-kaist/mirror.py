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
import { renderForm, packageTabForPath, refreshFormSummaries } from './form.js';
import { findNodeAtLocation } from 'jsonc-parser';
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
      <button type="button" id="ce-copy" data-draft-action>Copy JSON</button>
      <button type="button" id="ce-view-mode" aria-pressed="false">Settings only</button>
      <button type="button" id="ce-nav-toggle" aria-expanded="true">Hide documentation navigation</button>
      <button type="button" id="ce-download" class="ce-primary" disabled>Download config.json</button>
      <input type="file" id="ce-file-input" accept=".json,application/json" hidden>
    </div>
    <div class="ce-statusbar"><span id="ce-status" role="status" aria-live="polite">Loading editor…</span><span id="ce-modified"></span></div>
    <p class="ce-privacy">Files stay in this browser tab. Nothing is uploaded or saved automatically. Passwords remain visible in the JSON.</p>
    <div class="ce-pane-tabs" role="tablist" aria-label="Editor pane">
      <button type="button" role="tab" aria-selected="false" tabindex="-1" aria-controls="ce-json-pane" id="ce-json-tab">JSON</button>
      <button type="button" role="tab" aria-selected="true" aria-controls="ce-settings-pane" id="ce-settings-tab">Settings</button>
    </div>
    <div class="ce-workspace" data-pane="settings">
      <section id="ce-json-pane" class="ce-pane" aria-label="JSON editor"><h2>config.json</h2><div id="ce-monaco"></div></section>
      <div id="ce-divider" role="separator" aria-label="Resize editor panes" aria-orientation="vertical" aria-valuemin="25" aria-valuemax="75" aria-valuenow="40" tabindex="0"></div>
      <section id="ce-settings-pane" class="ce-pane" aria-label="Configuration settings"><h2>Settings</h2><fieldset id="ce-form"></fieldset></section>
    </div>
    <div id="ce-draft-notice" role="alert" hidden><span id="ce-draft-message"></span> <button type="button" id="ce-discard-input" data-draft-action>Discard input</button> <button type="button" id="ce-reapply-input" data-draft-action hidden>Keep my input</button></div>
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
  let selectedTab = 'sync';
  let applying = false;
  let draft = null;
  let composing = false;
  let committing = false;
  const expandedGroups = new Map();
  const jsonHighlight = editor.createDecorationsCollection();
  let importVersion = 0;
  let current;
  let issues = [];

  function notice(message) { find('ce-notice').textContent = message; }

  function pathText(path) {
    const parsed = parseDocument(model.getValue());
    if (!parsed.valid) return undefined;
    const node = findNodeAtLocation(parsed.tree, path);
    return node ? model.getValue().slice(node.offset, node.offset + node.length) : undefined;
  }

  function beginDraft(node) {
    if (!node?.matches('input, textarea, select') || node.closest('dialog') || node.dataset.draftIgnore !== undefined) return;
    const key = node.dataset.path || node.dataset.typePath || node.dataset.durationPath;
    if (!key) return;
    if (!draft || draft.node !== node) {
      const path = JSON.parse(key);
      draft = { node, path, original: pathText(path), revision: model.getVersionId(), message: '' };
    }
    find('ce-modified').textContent = 'Modified';
  }

  function draftError(message, conflict = false) {
    if (!draft) return;
    draft.message = message;
    draft.conflict = conflict;
    draft.node.setAttribute('aria-invalid', 'true');
    find('ce-draft-message').textContent = message + ' Press Escape to discard.' + (conflict ? ' Press Ctrl+Enter to keep your input.' : '');
    find('ce-draft-notice').hidden = false;
    find('ce-reapply-input').hidden = !conflict;
    find('ce-download').disabled = true;
    annotateIssues();
  }

  function clearDraft() {
    draft?.node.removeAttribute('aria-invalid');
    draft = null;
    find('ce-draft-notice').hidden = true;
    find('ce-reapply-input').hidden = true;
  }

  function commitDraft() {
    if (!draft) return true;
    if (composing || committing) return false;
    if (model.getVersionId() !== draft.revision && pathText(draft.path) !== draft.original) {
      draftError('This value also changed in JSON. Discard this input or explicitly keep your input.', true);
      return false;
    }
    if (!draft.node.checkValidity() || (draft.node.type === 'number' && draft.node.value === '')) {
      draftError(draft.node.validationMessage || 'Enter a number, or discard this input.');
      return false;
    }
    committing = true;
    draft.node.dispatchEvent(new Event('change', { bubbles: true }));
    committing = false;
    if (draft) {
      if (!draft.message) draftError('Complete this input before continuing, or discard it.');
      return false;
    }
    return true;
  }

  function apply(edits, rebuild = true) {
    if (!edits.length) { clearDraft(); checkDocument(false); return; }
    applying = true;
    try {
      editor.pushUndoStop();
      editor.executeEdits('configuration-form', edits.map((edit) => {
        const start = model.getPositionAt(edit.offset);
        const end = model.getPositionAt(edit.offset + edit.length);
        return { range: new monaco.Range(start.lineNumber, start.column, end.lineNumber, end.column), text: edit.content, forceMoveMarkers: true };
      }));
      editor.pushUndoStop();
    } finally { applying = false; }
    clearDraft();
    checkDocument(rebuild);
  }

  function mutate(callback) {
    try { callback(); notice(''); } catch (error) { notice(error.message); }
  }

  function changeField(path, newValue) {
    if (composing) return;
    mutate(() => {
      if (draft && model.getVersionId() !== draft.revision && pathText(draft.path) !== draft.original) {
        draftError('This value also changed in JSON. Discard this input or explicitly keep your input.', true);
        return;
      }
      const text = model.getValue();
      const existing = findNodeAtLocation(parseDocument(text).tree, path);
      if (existing && newValue !== null && typeof newValue !== 'object' && Object.is(existing.value, newValue)) {
        clearDraft(); checkDocument(false); return;
      }
      const edits = editProperty(text, path, newValue);
      // Validate a candidate without converting unrelated numeric literals.
      let candidateText = text;
      for (const edit of [...edits].sort((a, b) => b.offset - a.offset)) {
        candidateText = candidateText.slice(0, edit.offset) + edit.content + candidateText.slice(edit.offset + edit.length);
      }
      const candidate = parseDocument(candidateText);
      const related = validateConfig(candidate.value).filter((issue) => issue.severity === 'error'
        && issue.path.length >= path.length && path.every((part, index) => issue.path[index] === part));
      if (draft && related.length) { draftError(related[0].message); return; }
      const previousNode = findNodeAtLocation(parseDocument(text).tree, path);
      const primitive = newValue === null || typeof newValue !== 'object';
      const structural = !previousNode || !primitive || path.at(-1) === 'synctype'
        || (previousNode.type === 'number' ? typeof newValue !== 'number' : previousNode.type !== typeof newValue)
        || Boolean(draft?.node.dataset.typePath) || Boolean(draft?.node.dataset.durationPath);
      apply(edits, structural);
    });
  }

  function revealJson(path, focus = false) {
    if (focus && !commitDraft()) return;
    const parsed = parseDocument(model.getValue());
    if (!parsed.valid) return;
    const position = locateIssue(parsed.tree, path);
    const start = model.getPositionAt(position.offset);
    const end = model.getPositionAt(position.offset + position.length);
    jsonHighlight.set([{ range: new monaco.Range(start.lineNumber, start.column, end.lineNumber, end.column),
      options: { isWholeLine: true, className: 'ce-json-highlight' } }]);
    editor.revealPositionNearTop(start);
    if (focus) {
      setSettingsOnly(false);
      selectPane('json');
      editor.setPosition(start);
      editor.focus();
    }
  }

  function render(value) {
    if (draft) return;
    const form = find('ce-form');
    const active = document.activeElement;
    const focusKey = active?.dataset?.path || active?.dataset?.typePath || active?.dataset?.durationPath;
    const focusLabel = active?.getAttribute('aria-label');
    const selection = active?.tagName === 'INPUT' && active.type === 'text'
      ? [active.selectionStart, active.selectionEnd] : null;
    const scroll = form.scrollTop;
    const groupKey = form.dataset.view || '';
    for (const item of form.querySelectorAll('details')) {
      expandedGroups.set(`${groupKey}:${item.dataset.path || item.querySelector('summary')?.textContent}`, item.open);
    }
    const viewKey = `${selectedPackage || '$global'}:${selectedTab}`;
    renderForm(form, value, {
      selectedPackage, selectedTab, issues,
      onSelectPackage(id) {
        if (!commitDraft()) return;
        selectedPackage = id; selectedTab = 'sync';
        render(current.value); form.scrollTop = 0;
        revealJson(id ? ['packages', id] : []);
      },
      onSelectTab(tab) {
        if (!commitDraft()) return;
        selectedTab = tab; render(current.value); form.scrollTop = 0;
        form.querySelector(`[data-tab="${tab}"]`)?.focus();
      },
      onShowJson(path) { revealJson(path, true); },
      onChange: changeField,
      onRemove(path) { if (commitDraft()) mutate(() => apply(editProperty(model.getValue(), path, undefined))); },
      onRename(oldId, newId) {
        if (!commitDraft()) return;
        mutate(() => {
          const edits = renamePackage(model.getValue(), oldId, newId);
          expandedGroups.set(`${newId}:${selectedTab}:Package actions`, true);
          selectedPackage = newId;
          apply(edits);
          revealJson(['packages', newId]);
        });
      },
      onAddPackage(id, method, packageValue) {
        if (!commitDraft()) return;
        mutate(() => {
          if (!id.trim()) throw new Error('Enter a package ID.');
          const parsed = parseDocument(model.getValue());
          if (!parsed.valid || !parsed.value || typeof parsed.value !== 'object') throw new Error('Repair JSON before adding a package.');
          if (Object.hasOwn(parsed.value.packages || {}, id)) throw new Error(`Package ID already exists: ${id}`);
          selectedPackage = id; selectedTab = 'sync';
          apply(editProperty(model.getValue(), ['packages', id], packageValue || createPackage(id, method)));
          form.scrollTop = 0;
          revealJson(['packages', id]);
        });
      },
      onDeletePackage(id) {
        if (!commitDraft()) return;
        if (window.confirm(`Delete package "${id}"?`)) mutate(() => {
          selectedPackage = undefined;
          apply(editProperty(model.getValue(), ['packages', id], undefined));
        });
      },
    });
    form.dataset.view = viewKey;
    for (const item of form.querySelectorAll('details')) {
      const key = `${viewKey}:${item.dataset.path || item.querySelector('summary')?.textContent}`;
      if (expandedGroups.has(key)) item.open = expandedGroups.get(key);
    }
    form.scrollTop = groupKey === viewKey ? scroll : 0;
    if (focusKey && groupKey === viewKey) {
      const fields = [...form.querySelectorAll('input, select, textarea')];
      const field = fields.find((item) => (item.dataset.path || item.dataset.typePath || item.dataset.durationPath) === focusKey
        && item.getAttribute('aria-label') === focusLabel);
      field?.focus({ preventScroll: true });
      if (selection && field?.type === 'text') field.setSelectionRange(...selection);
    }
    annotateIssues();
  }

  function annotateIssues() {
    const form = find('ce-form');
    if (current?.valid) refreshFormSummaries(form, current.value, issues);
    form.querySelectorAll('.ce-inline-issue').forEach((node) => node.remove());
    form.querySelectorAll('[data-validation-error]').forEach((node) => {
      node.removeAttribute('aria-invalid'); node.removeAttribute('aria-describedby'); delete node.dataset.validationError;
    });
    const allIssues = draft?.message ? [...issues, { path: draft.path, message: draft.message, severity: 'error' }] : issues;
    for (const issue of allIssues) {
      const key = JSON.stringify(issue.path);
      const field = [...form.querySelectorAll('input[data-path], select[data-path], textarea[data-path]')]
        .find((node) => node.dataset.path === key && !node.closest('dialog'));
      if (!field) continue;
      const wrapper = field.closest('.ce-field') || field.parentElement;
      const message = document.createElement('div');
      message.className = 'ce-inline-issue'; message.textContent = issue.message;
      message.id = `ce-validation-${form.querySelectorAll('.ce-inline-issue').length}`;
      wrapper.append(message);
      if (issue.severity === 'error') { field.setAttribute('aria-invalid', 'true'); field.dataset.validationError = 'true'; }
      field.setAttribute('aria-describedby', [field.getAttribute('aria-describedby'), message.id].filter(Boolean).join(' '));
    }
    for (const tab of form.querySelectorAll('[data-tab]')) {
      const count = allIssues.filter((issue) => issue.severity === 'error' && issue.path[0] === 'packages'
        && issue.path[1] === selectedPackage && packageTabForPath(issue.path) === tab.dataset.tab).length;
      tab.dataset.baseLabel ||= tab.textContent;
      tab.textContent = tab.dataset.baseLabel + (count ? ` (${count})` : '');
    }
  }

  function checkDocument(rebuild = true) {
    clearTimeout(timer);
    const text = model.getValue();
    current = parseDocument(text);
    issues = current.valid ? validateConfig(current.value) : current.issues;
    const errors = issues.filter((item) => item.severity === 'error');
    const warnings = issues.length - errors.length;
    find('ce-status').textContent = errors.length ? `${errors.length} error${errors.length === 1 ? '' : 's'} — repair before downloading`
      : warnings ? `Valid configuration — ${warnings} warning${warnings === 1 ? '' : 's'}` : 'Valid configuration';
    find('ce-status').dataset.state = errors.length ? 'error' : 'valid';
    find('ce-download').disabled = errors.length > 0 || Boolean(draft?.message);
    find('ce-format').disabled = !current.valid;
    find('ce-modified').textContent = text === baseline && !draft ? '' : 'Modified';
    const renderable = current.valid && current.value !== null && typeof current.value === 'object' && !Array.isArray(current.value);
    find('ce-form').disabled = !renderable;
    if (renderable && rebuild) render(current.value);
    else if (renderable) annotateIssues();
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
        if (!commitDraft()) return;
        const path = issue.path || [];
        if (current.valid && path.length) {
          selectedPackage = path[0] === 'packages' ? path[1] : undefined;
          selectedTab = packageTabForPath(path);
          render(current.value);
          const field = [...find('ce-form').querySelectorAll('[data-path]')].find((node) => node.dataset.path === JSON.stringify(path));
          if (field) {
            for (let parent = field.parentElement; parent && parent !== find('ce-form'); parent = parent.parentElement) {
              if (parent.tagName === 'DETAILS') parent.open = true;
            }
            selectPane('settings'); field.scrollIntoView({ block: 'nearest' }); field.focus(); return;
          }
        }
        setSettingsOnly(false); selectPane('json'); editor.revealPositionInCenter(start); editor.setPosition(start); editor.focus();
      });
      item.append(button); list.append(item);
    }
    monaco.editor.setModelMarkers(model, 'mirror-config', markers);
  }

  model.onDidChangeContent(() => {
    if (applying) return;
    clearTimeout(timer);
    if (draft && pathText(draft.path) !== draft.original) draftError('This value also changed in JSON. Discard this input or explicitly keep your input.', true);
    find('ce-status').textContent = 'Checking configuration…';
    find('ce-download').disabled = true;
    find('ce-form').disabled = true;
    timer = setTimeout(checkDocument, 250);
  });

  function replaceDocument(text) {
    if (!commitDraft()) return;
    if (model.getValue() !== baseline && !window.confirm('Replace the current configuration? Unsaved changes will be lost.')) return;
    baseline = text;
    selectedPackage = undefined; selectedTab = 'sync'; expandedGroups.clear();
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
  find('ce-format').addEventListener('click', () => { if (commitDraft()) mutate(() => apply(formatDocument(model.getValue()))); });
  find('ce-copy').addEventListener('click', async () => {
    try { await navigator.clipboard.writeText(model.getValue()); notice('JSON copied.'); }
    catch { editor.focus(); editor.setSelection(model.getFullModelRange()); notice('Clipboard access is unavailable. Copy the selected JSON with your keyboard.'); }
  });
  find('ce-download').addEventListener('click', () => {
    if (!commitDraft()) return;
    checkDocument(false);
    if (issues.some((item) => item.severity === 'error')) return;
    const text = model.getValue();
    const url = URL.createObjectURL(new Blob([text], { type: 'application/json' }));
    const link = document.createElement('a');
    link.href = url; link.download = 'config.json'; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    baseline = text; find('ce-modified').textContent = ''; notice('Configuration downloaded.');
  });

  function selectPane(name) {
    if (!commitDraft()) return;
    container.querySelector('.ce-workspace').dataset.pane = name;
    find('ce-json-tab').setAttribute('aria-selected', String(name === 'json'));
    find('ce-settings-tab').setAttribute('aria-selected', String(name === 'settings'));
    find('ce-json-tab').tabIndex = name === 'json' ? 0 : -1;
    find('ce-settings-tab').tabIndex = name === 'settings' ? 0 : -1;
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
  function setSettingsOnly(enabled) {
    container.classList.toggle('ce-settings-only', enabled);
    find('ce-view-mode').textContent = enabled ? 'Split view' : 'Settings only';
    find('ce-view-mode').setAttribute('aria-pressed', String(enabled));
    if (enabled) selectPane('settings');
    editor.layout();
  }
  find('ce-view-mode').addEventListener('click', () => {
    if (commitDraft()) setSettingsOnly(!container.classList.contains('ce-settings-only'));
  });
  find('ce-nav-toggle').addEventListener('click', () => {
    if (!commitDraft()) return;
    const hidden = document.body.classList.toggle('ce-nav-hidden');
    find('ce-nav-toggle').textContent = hidden ? 'Show documentation navigation' : 'Hide documentation navigation';
    find('ce-nav-toggle').setAttribute('aria-expanded', String(!hidden));
    editor.layout();
  });
  for (const tab of [find('ce-json-tab'), find('ce-settings-tab')]) {
    tab.addEventListener('keydown', (event) => {
      if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
      event.preventDefault();
      const name = event.key === 'Home' ? 'json' : event.key === 'End' ? 'settings'
        : tab.id === 'ce-json-tab' ? 'settings' : 'json';
      if (!commitDraft()) return;
      selectPane(name); find(`ce-${name}-tab`).focus();
    });
  }
  find('ce-form').addEventListener('compositionstart', (event) => { composing = true; beginDraft(event.target); });
  find('ce-form').addEventListener('compositionend', () => { composing = false; });
  find('ce-form').addEventListener('input', (event) => {
    beginDraft(event.target);
    if (draft) {
      draft.message = ''; find('ce-draft-notice').hidden = true;
      find('ce-download').disabled = issues.some((issue) => issue.severity === 'error');
    }
  });
  find('ce-form').addEventListener('change', (event) => {
    if (!committing) beginDraft(event.target);
  }, true);
  container.addEventListener('pointerdown', (event) => {
    if (!draft || event.target === draft.node || event.target.closest('[data-draft-action]')) return;
    if (!commitDraft()) { event.preventDefault(); event.stopPropagation(); draft?.node.focus(); }
  }, true);
  container.addEventListener('keydown', (event) => {
    if (!draft || event.target.closest('[data-draft-action]')) return;
    if (composing) { if (event.key === 'Tab') event.preventDefault(); return; }
    if (event.key === 'Enter' && (event.ctrlKey || event.metaKey) && draft.conflict) {
      event.preventDefault(); find('ce-reapply-input').click(); return;
    }
    if (event.key === 'Tab' || event.key === 'Enter') {
      if (!commitDraft()) { event.preventDefault(); event.stopPropagation(); }
    }
    if (event.key === 'Escape') { event.preventDefault(); find('ce-discard-input').click(); }
  }, true);
  find('ce-discard-input').addEventListener('click', () => { clearDraft(); checkDocument(); });
  find('ce-reapply-input').addEventListener('click', () => {
    if (!draft) return;
    draft.original = pathText(draft.path); draft.revision = model.getVersionId(); draft.conflict = false;
    commitDraft();
  });
  window.addEventListener('beforeunload', (event) => {
    if (model.getValue() !== baseline || draft) { event.preventDefault(); event.returnValue = ''; }
  });
  checkDocument();
}
