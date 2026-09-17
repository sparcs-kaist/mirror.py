import { configSchema, methodSchemas, packageSchema } from "./schema.js";

const hasOwn = (value, key) =>
  value !== null &&
  typeof value === "object" &&
  Object.prototype.hasOwnProperty.call(value, key);

const unsafeKeys = new Set(["__proto__", "constructor", "prototype"]);

let controlCounter = 0;

function element(tagName, className, text) {
  const node = document.createElement(tagName);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function errorElement(text) {
  const node = element("div", "ce-error", text);
  node.setAttribute("role", "alert");
  return node;
}

function pathValue(path) {
  return JSON.stringify(path);
}

function setPath(node, path) {
  node.dataset.path = pathValue(path);
  return node;
}

function controlId(path) {
  controlCounter += 1;
  let hash = 2166136261;
  for (const character of pathValue(path)) {
    hash ^= character.charCodeAt(0);
    hash = Math.imul(hash, 16777619);
  }
  return `ce-control-${(hash >>> 0).toString(36)}-${controlCounter}`;
}

function labelFor(schema, fallback) {
  return schema?.["x-label"] || schema?.title || humanize(fallback);
}

function humanize(value) {
  return String(value ?? "Value")
    .replace(/[_-]+/g, " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function clone(value) {
  if (typeof structuredClone === "function") return structuredClone(value);
  return JSON.parse(JSON.stringify(value));
}

function schemaTypes(schema) {
  if (!schema) return [];
  if (Array.isArray(schema.type)) return schema.type;
  if (schema.type) return [schema.type];
  const variants = schema.anyOf || schema.oneOf;
  if (Array.isArray(variants)) {
    return [...new Set(variants.flatMap((variant) => schemaTypes(variant)))];
  }
  if (schema.properties || schema.additionalProperties) return ["object"];
  if (schema.items) return ["array"];
  return [];
}

function schemaForType(schema, type) {
  const variants = schema?.anyOf || schema?.oneOf;
  if (!Array.isArray(variants)) return schema;
  return variants.find((variant) => schemaTypes(variant).includes(type)) || schema;
}

function valueType(value) {
  if (Array.isArray(value)) return "array";
  if (value === null) return "null";
  if (Number.isInteger(value)) return "integer";
  return typeof value;
}

function acceptsValue(schema, value) {
  const variants = schema?.anyOf || schema?.oneOf;
  if (Array.isArray(variants)) {
    return variants.some((variant) => acceptsValue(variant, value));
  }
  const types = schemaTypes(schema);
  if (types.length === 0) return true;
  const actual = valueType(value);
  if (actual === "integer" && types.includes("number")) return true;
  return types.includes(actual);
}

function defaultValue(schema) {
  if (hasOwn(schema, "default")) return clone(schema.default);
  if (Array.isArray(schema?.enum) && schema.enum.length > 0) {
    return clone(schema.enum[0]);
  }

  const type = schemaTypes(schema)[0];
  if (type === "object") {
    const result = {};
    for (const key of schema?.required || []) {
      const childSchema = schema?.properties?.[key];
      if (childSchema && !unsafeKeys.has(key)) result[key] = defaultValue(childSchema);
    }
    return result;
  }
  if (type === "array") return [];
  if (type === "boolean") return false;
  if (type === "integer" || type === "number") return 0;
  return "";
}

function addHelp(parent, schema) {
  const parts = [];
  if (schema?.description) parts.push(schema.description);
  if (hasOwn(schema, "default")) {
    parts.push(`Default: ${JSON.stringify(schema.default)}`);
  }
  if (parts.length > 0) parent.append(element("div", "ce-help", parts.join(" ")));
}

function addRemoveButton(parent, path, context, label) {
  if (!context.removable) return;
  const button = element("button", "ce-button", `Remove ${label}`);
  button.type = "button";
  button.disabled = context.disabled;
  button.addEventListener("click", () => context.onRemove(path));
  parent.append(button);
}

function renderMalformed(parent, path, label, schema, value, context) {
  const wrapper = setPath(element("div", "ce-field"), path);
  const row = element("div", "ce-row");
  row.append(element("strong", "", label));
  addRemoveButton(row, path, context, label);
  wrapper.append(row);
  const expected = schemaTypes(schema).join(" or ") || "a supported value";
  wrapper.append(
    errorElement(
      `Expected ${expected}; found ${valueType(value)}. Edit this value in JSON.`,
    ),
  );
  addHelp(wrapper, schema);
  parent.append(wrapper);
}

function renderMissing(parent, path, label, schema, context) {
  const wrapper = setPath(element("div", "ce-field"), path);
  const row = element("div", "ce-row");
  row.append(element("span", "", label));
  const button = element(
    "button",
    "ce-button",
    hasOwn(schema, "default") ? "Use default" : `Add ${label}`,
  );
  button.type = "button";
  button.disabled = context.disabled;
  button.setAttribute("aria-label", `Add ${label}`);
  button.addEventListener("click", () => context.onChange(path, defaultValue(schema)));
  row.append(button);
  wrapper.append(row);
  if (context.required) {
    wrapper.append(errorElement(`${label} is required.`));
  }
  addHelp(wrapper, schema);
  parent.append(wrapper);
}

function renderStringOrArray(parent, path, label, schema, value, context) {
  const wrapper = element("div", "ce-field");
  const id = controlId(path);
  const row = element("div", "ce-row");
  const fieldLabel = element("label", "", label);
  fieldLabel.htmlFor = id;
  row.append(fieldLabel);

  const kind = element("select", "ce-input");
  kind.id = id;
  kind.disabled = context.disabled;
  kind.setAttribute("aria-label", `${label} value type`);
  kind.dataset.typePath = pathValue(path);
  const singleOption = element("option", "", "Single value");
  singleOption.value = "string";
  const listOption = element("option", "", "List");
  listOption.value = "array";
  kind.append(singleOption, listOption);
  kind.value = Array.isArray(value) ? "array" : "string";
  row.append(kind);
  addRemoveButton(row, path, context, label);
  wrapper.append(row);
  addHelp(wrapper, schema);

  const error = errorElement();
  error.hidden = true;
  wrapper.append(error);
  kind.addEventListener("change", () => {
    if (kind.value === "array") {
      context.onChange(path, value === "" ? [] : [value]);
      return;
    }
    if (value.length > 1) {
      error.textContent = "Remove all but one list item before using a single value.";
      error.hidden = false;
      kind.value = "array";
      return;
    }
    context.onChange(path, value[0] ?? "");
  });

  const childSchema = schemaForType(schema, kind.value);
  if (kind.value === "array") {
    renderArray(wrapper, path, "Items", childSchema, value, {
      ...context,
      removable: false,
    });
  } else {
    renderPrimitive(wrapper, path, "Value", childSchema, value, {
      ...context,
      removable: false,
    });
  }
  parent.append(wrapper);
}

function renderPrimitiveUnion(parent, path, label, schema, value, context) {
  const wrapper = element("div", "ce-field");
  const id = controlId(path);
  const row = element("div", "ce-row");
  const fieldLabel = element("label", "", label);
  fieldLabel.htmlFor = id;
  row.append(fieldLabel);

  const types = schemaTypes(schema);
  const typeSelect = element("select", "ce-input");
  typeSelect.id = id;
  typeSelect.disabled = context.disabled;
  typeSelect.setAttribute("aria-label", `${label} value type`);
  typeSelect.dataset.typePath = pathValue(path);
  for (const type of types) {
    const option = element("option", "", humanize(type));
    option.value = type;
    typeSelect.append(option);
  }
  const actualType = valueType(value);
  typeSelect.value = types.includes(actualType)
    ? actualType
    : actualType === "integer" && types.includes("number")
      ? "number"
      : types[0];
  row.append(typeSelect);
  addRemoveButton(row, path, context, label);
  wrapper.append(row);
  addHelp(wrapper, schema);

  typeSelect.addEventListener("change", () => {
    const nextSchema = schemaForType(schema, typeSelect.value);
    let nextValue = defaultValue(nextSchema);
    if (typeSelect.value === "string") nextValue = String(value);
    if (typeSelect.value === "integer" || typeSelect.value === "number") {
      const numeric = Number(value);
      if (Number.isFinite(numeric)) nextValue = numeric;
    }
    context.onChange(path, nextValue);
  });

  renderPrimitive(
    wrapper,
    path,
    "Value",
    schemaForType(schema, typeSelect.value),
    value,
    { ...context, removable: false },
  );
  parent.append(wrapper);
}

function renderPrimitive(parent, path, label, schema, value, context) {
  const wrapper = element("div", "ce-field");
  const row = element("div", "ce-row");
  const id = controlId(path);
  const fieldLabel = element("label", "", label);
  fieldLabel.htmlFor = id;
  row.append(fieldLabel);

  let input;
  if (Array.isArray(schema?.enum)) {
    input = element("select", "ce-input");
    for (const enumValue of schema.enum) {
      const option = element("option", "", String(enumValue));
      option.value = JSON.stringify(enumValue);
      input.append(option);
    }
    if (!schema.enum.some((item) => Object.is(item, value))) {
      const option = element("option", "", `Unknown: ${String(value)}`);
      option.value = JSON.stringify(value);
      input.prepend(option);
    }
    input.value = JSON.stringify(value);
    input.addEventListener("change", () => context.onChange(path, JSON.parse(input.value)));
  } else if (schemaTypes(schema).includes("boolean")) {
    input = element("input", "ce-input");
    input.type = "checkbox";
    input.checked = value;
    input.addEventListener("change", () => context.onChange(path, input.checked));
  } else {
    input = element("input", "ce-input");
    const numeric = ["integer", "number"].some((type) => schemaTypes(schema).includes(type));
    input.type = numeric
      ? "number"
      : schema?.format === "password" || schema?.["x-secret"]
        ? "password"
        : "text";
    input.value = String(value);
    if (numeric) {
      if (schemaTypes(schema).includes("integer")) input.step = "1";
      if (schema?.minimum !== undefined) input.min = String(schema.minimum);
      if (schema?.maximum !== undefined) input.max = String(schema.maximum);
      input.addEventListener("change", () => {
        if (input.value === "" || !input.checkValidity()) return;
        const next = Number(input.value);
        if (Number.isFinite(next)) context.onChange(path, next);
      });
    } else {
      input.addEventListener("change", () => context.onChange(path, input.value));
    }
  }

  input.id = id;
  input.disabled = context.disabled;
  input.setAttribute("aria-label", label);
  setPath(input, path);
  row.append(input);
  addRemoveButton(row, path, context, label);
  wrapper.append(row);
  addHelp(wrapper, schema);
  parent.append(wrapper);
}

function renderArray(parent, path, label, schema, value, context) {
  const details = setPath(element("details", "ce-group"), path);
  details.open = true;
  const summary = element("summary", "", `${label} (${value.length})`);
  details.append(summary);
  addHelp(details, schema);
  addRemoveButton(details, path, context, label);

  const items = schema?.items || {};
  value.forEach((item, index) => {
    renderNode(details, [...path, index], `Item ${index + 1}`, items, item, {
      ...context,
      removable: true,
      required: true,
    });
  });

  const add = element("button", "ce-button", `Add ${label} item`);
  add.type = "button";
  add.disabled = context.disabled;
  add.addEventListener("click", () =>
    context.onChange([...path, value.length], defaultValue(items)),
  );
  details.append(add);
  parent.append(details);
}

function renderUnknown(parent, path, key, value, context) {
  const wrapper = setPath(element("div", "ce-field"), path);
  const row = element("div", "ce-row");
  row.append(element("strong", "", humanize(key)));
  wrapper.append(row);
  wrapper.append(
    element(
      "div",
      "ce-help",
      `Unknown key (${valueType(value)}) is preserved. Edit its value in JSON.`,
    ),
  );
  parent.append(wrapper);
}

function renderDynamicProperties(parent, path, schema, value, knownKeys, context) {
  const extras = Object.keys(value).filter((key) => !knownKeys.has(key));
  const additionalSchema =
    schema?.additionalProperties && typeof schema.additionalProperties === "object"
      ? schema.additionalProperties
      : null;

  for (const key of extras) {
    if (additionalSchema) {
      renderNode(parent, [...path, key], humanize(key), additionalSchema, value[key], {
        ...context,
        removable: true,
        required: false,
      });
    } else {
      renderUnknown(parent, [...path, key], key, value[key], context);
    }
  }

  if (!additionalSchema) return;
  const row = element("div", "ce-row");
  const input = element("input", "ce-input");
  input.type = "text";
  input.placeholder = "New key";
  input.disabled = context.disabled;
  input.setAttribute("aria-label", `New ${labelFor(schema, "entry")} key`);
  const add = element("button", "ce-button", "Add entry");
  add.type = "button";
  add.disabled = context.disabled;
  const error = errorElement();
  error.hidden = true;
  add.addEventListener("click", () => {
    const key = input.value.trim();
    if (!key || unsafeKeys.has(key) || hasOwn(value, key)) {
      error.textContent = "Enter a unique, safe key.";
      error.hidden = false;
      return;
    }
    context.onChange([...path, key], defaultValue(additionalSchema));
  });
  row.append(input, add);
  parent.append(row, error);
}

function renderObjectProperties(parent, path, schema, value, context) {
  const properties = schema?.properties || {};
  const required = new Set(schema?.required || []);
  const grouped = new Map();

  for (const [key, childSchema] of Object.entries(properties)) {
    const group = childSchema?.["x-group"] || "";
    if (!grouped.has(group)) grouped.set(group, []);
    grouped.get(group).push([key, childSchema]);
  }

  const renderEntries = (target, entries) => {
    for (const [key, childSchema] of entries) {
      const childPath = [...path, key];
      const label = labelFor(childSchema, key);
      if (!hasOwn(value, key)) {
        renderMissing(target, childPath, label, childSchema, {
          ...context,
          required: required.has(key),
        });
      } else {
        renderNode(target, childPath, label, childSchema, value[key], {
          ...context,
          removable: !required.has(key),
          required: required.has(key),
        });
      }
    }
  };

  for (const [group, entries] of grouped) {
    if (!group) {
      renderEntries(parent, entries);
      continue;
    }
    const details = element("details", "ce-section");
    details.open = true;
    details.append(element("summary", "", group));
    renderEntries(details, entries);
    parent.append(details);
  }

  renderDynamicProperties(parent, path, schema, value, new Set(Object.keys(properties)), context);
}

function renderObject(parent, path, label, schema, value, context) {
  const details = setPath(element("details", "ce-group"), path);
  details.open = true;
  details.append(element("summary", "", label));
  addHelp(details, schema);
  addRemoveButton(details, path, context, label);
  renderObjectProperties(details, path, schema, value, context);
  parent.append(details);
}

function renderNode(parent, path, label, schema, value, context) {
  if (!acceptsValue(schema, value)) {
    renderMalformed(parent, path, label, schema, value, context);
    return;
  }

  const types = schemaTypes(schema);
  if (types.includes("string") && types.includes("array")) {
    renderStringOrArray(parent, path, label, schema, value, context);
  } else if (types.length > 1 && !types.includes("object") && !types.includes("array")) {
    renderPrimitiveUnion(parent, path, label, schema, value, context);
  } else if (Array.isArray(value)) {
    renderArray(parent, path, label, schemaForType(schema, "array"), value, context);
  } else if (value !== null && typeof value === "object") {
    renderObject(parent, path, label, schemaForType(schema, "object"), value, context);
  } else {
    renderPrimitive(parent, path, label, schema, value, context);
  }
}

function effectivePackageSchema(packageValue) {
  const method = packageValue?.synctype;
  const optionsSchema = typeof method === "string" && hasOwn(methodSchemas, method) ? methodSchemas[method] : null;
  const methodField = packageSchema?.properties?.synctype || { type: "string" };
  const withMethodChoices = {
    ...packageSchema,
    properties: {
      ...packageSchema.properties,
      synctype: { ...methodField, enum: Object.keys(methodSchemas) },
    },
  };
  if (!optionsSchema) return withMethodChoices;

  const settingsSchema = withMethodChoices?.properties?.settings;
  if (!settingsSchema) return withMethodChoices;
  return {
    ...withMethodChoices,
    properties: {
      ...withMethodChoices.properties,
      settings: {
        ...settingsSchema,
        properties: {
          ...settingsSchema.properties,
          options: optionsSchema,
        },
      },
    },
  };
}

function renderPackageBar(parent, packages, selectedPackage, context) {
  const bar = element("div", "ce-package-bar");
  const selectLabel = element("label", "", "Configuration section");
  const select = element("select", "ce-input");
  const selectId = controlId(["packages"]);
  select.id = selectId;
  selectLabel.htmlFor = selectId;
  const globalOption = element("option", "", "Global settings");
  globalOption.value = "";
  select.append(globalOption);
  for (const packageId of Object.keys(packages)) {
    const option = element("option", "", packageId);
    option.value = packageId;
    select.append(option);
  }
  select.value = selectedPackage || "";
  select.disabled = context.disabled;
  select.addEventListener("change", () => context.onSelectPackage?.(select.value || null));
  bar.append(selectLabel, select);

  const packageId = element("input", "ce-input");
  packageId.type = "text";
  packageId.placeholder = "package-id";
  packageId.setAttribute("aria-label", "New package ID");
  packageId.disabled = context.disabled;

  const method = element("select", "ce-input");
  method.setAttribute("aria-label", "New package sync type");
  method.disabled = context.disabled;
  for (const methodName of Object.keys(methodSchemas)) {
    const option = element("option", "", methodName);
    option.value = methodName;
    method.append(option);
  }

  const add = element("button", "ce-button", "Add package");
  add.type = "button";
  add.disabled = context.disabled;
  const error = errorElement();
  error.hidden = true;
  add.addEventListener("click", () => {
    const id = packageId.value.trim();
    if (!id || unsafeKeys.has(id) || hasOwn(packages, id)) {
      error.textContent = "Enter a unique, safe package ID.";
      error.hidden = false;
      return;
    }
    context.onAddPackage(id, method.value);
  });
  bar.append(packageId, method, add, error);
  parent.append(bar);
}

function renderSelectedPackage(parent, packages, packageId, context) {
  const value = packages[packageId];
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    renderMalformed(parent, ["packages", packageId], packageId, packageSchema, value, {
      ...context,
      removable: false,
    });
    return;
  }

  const section = element("section", "ce-section");
  const title = element("h2", "", `Package: ${packageId}`);
  section.append(title);

  const row = element("div", "ce-row");
  const renameLabel = element("label", "", "Package ID");
  const rename = element("input", "ce-input");
  const renameId = controlId(["packages", packageId, "id"]);
  renameLabel.htmlFor = renameId;
  rename.id = renameId;
  rename.type = "text";
  rename.value = packageId;
  rename.disabled = context.disabled;
  rename.setAttribute("aria-label", "Package ID");
  setPath(rename, ["packages", packageId, "id"]);
  const renameButton = element("button", "ce-button", "Rename package");
  renameButton.type = "button";
  renameButton.disabled = context.disabled;
  const deleteButton = element("button", "ce-button", "Delete package");
  deleteButton.type = "button";
  deleteButton.disabled = context.disabled;
  const error = errorElement();
  error.hidden = true;
  renameButton.addEventListener("click", () => {
    const nextId = rename.value.trim();
    if (
      !nextId ||
      unsafeKeys.has(nextId) ||
      (nextId !== packageId && hasOwn(packages, nextId))
    ) {
      error.textContent = "Enter a unique, safe package ID.";
      error.hidden = false;
      return;
    }
    if (nextId !== packageId) context.onRename(packageId, nextId);
  });
  deleteButton.addEventListener("click", () => context.onDeletePackage(packageId));
  row.append(renameLabel, rename, renameButton, deleteButton);
  section.append(row, error);

  const schema = effectivePackageSchema(value);
  const properties = { ...(schema.properties || {}) };
  delete properties.id;
  const formValue = { ...value };
  delete formValue.id;
  renderObjectProperties(
    section,
    ["packages", packageId],
    { ...schema, properties },
    formValue,
    context,
  );
  parent.append(section);
}

function renderGlobal(parent, value, context) {
  const section = element("section", "ce-section");
  section.append(element("h2", "", "Global settings"));
  const properties = { ...(configSchema?.properties || {}) };
  delete properties.packages;
  const formValue = { ...value };
  delete formValue.packages;
  renderObjectProperties(section, [], { ...configSchema, properties }, formValue, context);
  parent.append(section);
}

export function renderForm(container, value, callbacks) {
  controlCounter = 0;
  container.replaceChildren();
  const context = {
    onChange: callbacks.onChange,
    onRemove: callbacks.onRemove,
    onRename: callbacks.onRename,
    onAddPackage: callbacks.onAddPackage,
    onDeletePackage: callbacks.onDeletePackage,
    onSelectPackage: callbacks.onSelectPackage,
    disabled: Boolean(callbacks.disabled),
    removable: false,
    required: true,
  };

  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    renderMalformed(container, [], "Configuration", configSchema, value, context);
    return;
  }

  const packages = hasOwn(value, "packages") ? value.packages : {};
  const packagesValid =
    packages !== null && typeof packages === "object" && !Array.isArray(packages);
  const safePackages = packagesValid ? packages : {};
  const selected =
    callbacks.selectedPackage && hasOwn(safePackages, callbacks.selectedPackage)
      ? callbacks.selectedPackage
      : null;

  renderPackageBar(container, safePackages, selected, context);
  if (!packagesValid) {
    renderMalformed(
      container,
      ["packages"],
      "Packages",
      configSchema?.properties?.packages || { type: "object" },
      packages,
      context,
    );
  }

  if (selected) renderSelectedPackage(container, safePackages, selected, context);
  else renderGlobal(container, value, context);
}
