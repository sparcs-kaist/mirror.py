import assert from 'node:assert/strict';
import test from 'node:test';
import { applyDocumentEdits, editProperty, formatDocument, parseDocument, renamePackage } from '../src/document.js';

test('strict JSON rejects comments, trailing commas and nested duplicate keys', () => {
  for (const text of ['{"a":1,}', '{/*comment*/"a":1}', '{"a":{"b":1,"b":2}}', '']) {
    assert.equal(parseDocument(text).valid, false);
  }
  assert.equal(parseDocument('{"a":1,"b":{"a":2}}').valid, true);
});

test('targeted edits retain unknown values, whitespace and large integer tokens', () => {
  const text = '{ "name": "before", "future": 999999999999999999999, "unknown": {"x":true} }';
  const changed = applyDocumentEdits(text, editProperty(text, ['name'], 'after'));
  assert.equal(changed, text.replace('before', 'after'));
});

test('omission, false and an empty string remain different', () => {
  let text = '{}';
  for (const value of [false, '', undefined]) {
    text = applyDocumentEdits(text, editProperty(text, ['option'], value));
    const data = JSON.parse(text);
    if (value === undefined) assert.equal(Object.hasOwn(data, 'option'), false);
    else assert.equal(data.option, value);
  }
});

test('rename changes both identifiers without reserializing the package', () => {
  const text = '{"packages":{"old":{"id":"old","future":999999999999999999999}}}';
  const changed = applyDocumentEdits(text, renamePackage(text, 'old', 'new'));
  assert.equal(changed, '{"packages":{"new":{"id":"new","future":999999999999999999999}}}');
  assert.throws(() => renamePackage('{"packages":{"a":{},"b":{}}}', 'a', 'b'), /already exists/);
});

test('literal path segments and prototype-looking keys are data', () => {
  const text = '{"__proto__":{"polluted":true},"a.b":{}}';
  const changed = applyDocumentEdits(text, editProperty(text, ['a.b', 'constructor'], 'value'));
  assert.equal(JSON.parse(changed)['a.b'].constructor, 'value');
  assert.equal({}.polluted, undefined);
  assert.equal(Object.hasOwn(parseDocument(text).value, '__proto__'), true);
});

test('formatting retains numeric tokens and refuses broken drafts', () => {
  const text = '{"number":999999999999999999999}';
  assert.match(applyDocumentEdits(text, formatDocument(text)), /999999999999999999999/);
  assert.throws(() => editProperty('{', ['x'], 1), /Repair JSON/);
});


test('renaming a package repairs a missing id without overlapping edits', () => {
  const text = '{"packages":{"old":{"unknown":9007199254740993}}}';
  const result = applyDocumentEdits(text, renamePackage(text, 'old', 'new'));
  assert.equal(JSON.parse(result).packages.new.id, 'new');
  assert.ok(result.includes('9007199254740993'));
  assert.equal(JSON.parse(applyDocumentEdits('{"packages":{"old":{}}}', renamePackage('{"packages":{"old":{}}}', 'old', 'new'))).packages.new.id, 'new');
});
