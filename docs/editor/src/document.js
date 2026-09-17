import { applyEdits, findNodeAtLocation, format, modify, parseTree, printParseErrorCode } from 'jsonc-parser';

const formattingOptions = { insertSpaces: true, tabSize: 2, eol: '\n' };

export function parseDocument(text) {
  const errors = [];
  const tree = parseTree(text, errors, { disallowComments: true, allowTrailingComma: false });
  const issues = errors.map((error) => ({
    path: [], severity: 'error', offset: error.offset, length: error.length,
    message: printParseErrorCode(error.error),
  }));
  function checkDuplicates(node, path = []) {
    if (node.type === 'object') {
      const keys = new Set();
      for (const property of node.children || []) {
        const [key, value] = property.children;
        if (keys.has(key.value)) issues.push({
          path: [...path, key.value], severity: 'error', offset: key.offset,
          length: key.length, message: `Duplicate key: ${key.value}`,
        });
        keys.add(key.value);
        if (value) checkDuplicates(value, [...path, key.value]);
      }
    } else if (node.type === 'array') {
      (node.children || []).forEach((child, index) => checkDuplicates(child, [...path, index]));
    }
  }
  if (tree && !issues.length) checkDuplicates(tree);
  if (!tree && !issues.length) issues.push({ path: [], severity: 'error', offset: 0, length: 0, message: 'Enter a JSON object.' });
  if (issues.length) return { value: null, tree, issues, valid: false };
  try {
    return { value: JSON.parse(text), tree, issues, valid: true };
  } catch (error) {
    return { value: null, tree, valid: false, issues: [{ path: [], severity: 'error', offset: 0, length: 0, message: error.message }] };
  }
}

export function editProperty(text, path, value) {
  if (!parseDocument(text).valid) throw new Error('Repair JSON syntax before editing the form.');
  return modify(text, path, value, { formattingOptions });
}

export function renamePackage(text, oldId, newId) {
  const parsed = parseDocument(text);
  if (!parsed.valid) throw new Error('Repair JSON syntax before renaming a package.');
  const packages = findNodeAtLocation(parsed.tree, ['packages']);
  if (packages?.type !== 'object') throw new Error('packages must be an object.');
  if (!newId.trim()) throw new Error('Enter a package ID.');
  if (newId !== oldId && packages.children.some((p) => p.children[0].value === newId)) {
    throw new Error(`Package ID already exists: ${newId}`);
  }
  const property = packages.children.find((p) => p.children[0].value === oldId);
  if (!property || property.children[1]?.type !== 'object') throw new Error('Select a valid package.');
  const key = property.children[0];
  const packageNode = property.children[1];
  const idNode = findNodeAtLocation(packageNode, ['id']);
  const idEdits = idNode
    ? [{ offset: idNode.offset, length: idNode.length, content: JSON.stringify(newId) }]
    : [{ offset: packageNode.offset + 1, length: 0, content: `"id": ${JSON.stringify(newId)}${packageNode.children.length ? ',' : ''}` }];
  return [...idEdits, { offset: key.offset, length: key.length, content: JSON.stringify(newId) }];
}

export function formatDocument(text) {
  if (!parseDocument(text).valid) throw new Error('Repair JSON syntax before formatting.');
  return format(text, undefined, formattingOptions);
}

export function applyDocumentEdits(text, edits) {
  return applyEdits(text, edits);
}

export function locateIssue(tree, path) {
  let node = tree;
  for (let length = path.length; length >= 0; length--) {
    const found = findNodeAtLocation(tree, path.slice(0, length));
    if (found) { node = found; break; }
  }
  return { offset: node?.offset || 0, length: node?.length || 0 };
}
