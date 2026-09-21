import { configSchema, methodSchemas, packageSchema, createPackage, validateConfig } from "./schema.js";

const hasOwn = (value, key) =>
  value !== null &&
  typeof value === "object" &&
  Object.prototype.hasOwnProperty.call(value, key);

const unsafeKeys = new Set(["__proto__", "constructor", "prototype"]);

let controlCounter = 0;
const packageBarStates = new WeakMap();

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
    hasOwn(schema, "default") ? context.required ? "Use default" : "Customize" : `Add ${label}`,
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
  if (!context.required) wrapper.append(element("div", "ce-help", "Not set in JSON; using the runtime default."));
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
      const current = wrapper.querySelector("input[data-path]")?.value ?? value;
      context.onChange(path, current === "" ? [] : [current]);
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
    const control = wrapper.querySelector("input[data-path], select[data-path]");
    const current = control ? control.type === "checkbox" ? control.checked : control.tagName === "SELECT" ? JSON.parse(control.value) : control.value : value;
    if (typeSelect.value === "string") nextValue = String(current);
    if (typeSelect.value === "integer" || typeSelect.value === "number") {
      const numeric = Number(current);
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
      input.required = true;
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
  showJson(row, path, context);
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
    const itemContext = { ...context, removable: true, required: true };
    const itemPath = [...path, index];
    if (path.at(-1) === "config" && item && typeof item === "object" && !Array.isArray(item)) {
      const repository = setPath(element("details", "ce-group ce-repository"), itemPath);
      repository.append(element("summary", "", typeof item.src === "string" && item.src ? item.src : `Repository ${index + 1}`));
      renderObjectProperties(repository, itemPath, items, item, itemContext);
      addRemoveButton(repository, itemPath, itemContext, `repository ${index + 1}`);
      details.append(repository);
    } else renderNode(details, itemPath, `Item ${index + 1}`, items, item, itemContext);
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
  const preview = value !== null && typeof value === "object"
    ? Array.isArray(value) ? `${value.length} items` : `${Object.keys(value).length} fields`
    : String(JSON.stringify(value)).slice(0, 120);
  wrapper.append(element("div", "ce-help", `${preview} — unknown ${valueType(value)} value is preserved. Edit in JSON.`));
  showJson(wrapper, path, context);
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

const packageTabs = { sync: "Sync", basic: "Basic information", links: "Links", advanced: "Advanced" };

export function packageTabForPath(path) {
  const key = path[2];
  if (["name", "href"].includes(key) || (key === "settings" && path[3] === "hidden")) return "basic";
  if (key === "link") return "links";
  if (["synctype", "syncrate", "disabled"].includes(key) || (key === "settings" && ["src", "dst", "options"].includes(path[3]))) return "sync";
  return "advanced";
}

function showJson(parent, path, context) {
  if (!context.onShowJson) return;
  const button = element("button", "ce-json-link", "JSON");
  button.type = "button";
  button.setAttribute("aria-label", "Show in JSON");
  button.addEventListener("click", () => context.onShowJson(path));
  parent.append(button);
}

function renderDuration(parent, path, value, context) {
  const match = typeof value === "string" && /^(?:P(\d+)D|PT(\d+)([HMS]))$/.exec(value);
  if (!match) {
    renderPrimitive(parent, path, "Sync interval (ISO 8601, PUSH, or empty)", packageSchema.properties.syncrate, value, context);
    return;
  }
  const row = element("div", "ce-field ce-duration");
  const label = element("label", "", "Sync interval");
  const input = setPath(element("input", "ce-input"), path);
  input.type = "number";
  input.min = "0";
  input.step = "1";
  input.required = true;
  input.id = controlId(path);
  input.value = match[1] || match[2];
  input.setAttribute("aria-label", "Sync interval");
  label.htmlFor = input.id;
  const unit = element("select", "ce-input");
  unit.setAttribute("aria-label", "Sync interval unit");
  unit.dataset.durationPath = pathValue(path);
  for (const [key, text] of Object.entries({ D: "Days", H: "Hours", M: "Minutes", S: "Seconds" })) {
    const option = element("option", "", text); option.value = key; unit.append(option);
  }
  unit.value = match[1] ? "D" : match[3];
  const update = () => {
    if (!input.checkValidity()) return;
    context.onChange(path, `${unit.value === "D" ? "P" : "PT"}${input.value}${unit.value}`);
  };
  input.addEventListener("change", update); unit.addEventListener("change", update);
  row.append(label, input, unit);
  const originalAmount = input.value;
  const originalUnit = unit.value;
  const raw = element("button", "ce-button", "Edit ISO 8601"); raw.type = "button";
  raw.addEventListener("click", () => {
    if (!input.checkValidity()) { input.reportValidity(); return; }
    const current = input.value === originalAmount && unit.value === originalUnit ? value : `${unit.value === "D" ? "P" : "PT"}${input.value}${unit.value}`;
    const replacement = element("div");
    renderPrimitive(replacement, path, "Sync interval (ISO 8601, PUSH, or empty)", packageSchema.properties.syncrate, current, context);
    row.replaceWith(...replacement.childNodes);
    [...parent.querySelectorAll("input[data-path]")].find((node) => node.dataset.path === pathValue(path))?.focus();
  });
  row.append(raw);
  showJson(row, path, context);
  parent.append(row);
}

function renderPackageFields(parent, packageId, value, context, tab = "sync") {
  const schema = effectivePackageSchema(value);
  const base = ["packages", packageId];
  const field = (key, owner = value, ownerSchema = schema, prefix = base) => {
    const descriptor = ownerSchema.properties?.[key];
    if (!descriptor) return;
    const path = [...prefix, key];
    const childContext = { ...context, required: ownerSchema.required?.includes(key), removable: !ownerSchema.required?.includes(key) };
    if (!hasOwn(owner, key)) renderMissing(parent, path, labelFor(descriptor, key), descriptor, childContext);
    else if (key === "syncrate" && typeof owner[key] === "string") renderDuration(parent, path, owner[key], childContext);
    else renderNode(parent, path, labelFor(descriptor, key), descriptor, owner[key], childContext);
  };
  const settings = value.settings;
  const settingsValid = settings && typeof settings === "object" && !Array.isArray(settings);
  if (tab === "sync") {
    field("synctype");
    if (settingsValid) {
      if (value.synctype !== "local") field("src", settings, schema.properties.settings, [...base, "settings"]);
      field("dst", settings, schema.properties.settings, [...base, "settings"]);
    } else field("settings");
    field("syncrate"); field("disabled");
    if (settingsValid) {
      const options = element("details", "ce-section ce-method-options");
      options.dataset.section = "method-options";
      options.append(element("summary", "", `${typeof value.synctype === "string" ? value.synctype : "Method"} options`));
      const descriptor = schema.properties.settings.properties.options;
      const path = [...base, "settings", "options"];
      if (!hasOwn(settings, "options")) renderMissing(options, path, "Method options", descriptor, context);
      else if (settings.options && typeof settings.options === "object" && !Array.isArray(settings.options)) renderObjectProperties(options, path, descriptor, settings.options, context);
      else renderMalformed(options, path, "Method options", descriptor, settings.options, context);
      parent.append(options);
    }
  } else if (tab === "basic") {
    field("name"); field("href");
    if (settingsValid) field("hidden", settings, schema.properties.settings, [...base, "settings"]);
  } else if (tab === "links") {
    if (!Array.isArray(value.link)) { field("link"); return; }
    value.link.forEach((link, index) => {
      const row = element("div", "ce-link-row");
      const path = [...base, "link", index];
      if (link && typeof link === "object" && !Array.isArray(link)) {
        for (const key of ["rel", "href"]) {
          const descriptor = packageSchema.properties.link.items.properties[key];
          if (hasOwn(link, key)) renderNode(row, [...path, key], labelFor(descriptor, key), descriptor, link[key], { ...context, removable: false });
          else renderMissing(row, [...path, key], labelFor(descriptor, key), descriptor, { ...context, required: true });
        }
        renderDynamicProperties(row, path, packageSchema.properties.link.items, link, new Set(["rel", "href"]), context);
      } else renderMalformed(row, path, "Link", packageSchema.properties.link.items, link, context);
      addRemoveButton(row, path, { ...context, removable: true }, `link ${index + 1}`);
      parent.append(row);
    });
    const add = element("button", "ce-button", "Add link"); add.type = "button";
    add.addEventListener("click", () => context.onChange([...base, "link", value.link.length], { rel: "", href: "" }));
    parent.append(add);
  } else {
    const method = typeof value.synctype === "string" ? value.synctype : "Invalid method";
    parent.append(element("p", "ce-help", `Sync method: ${method}. Unknown and plugin-specific fields are preserved. Use JSON to edit them.`));
    showJson(parent, base, context);
    if (settingsValid && settings.options && typeof settings.options === "object" && !Array.isArray(settings.options)) {
      const known = hasOwn(methodSchemas, method) ? new Set(Object.keys(methodSchemas[method].properties)) : new Set();
      for (const [key, option] of Object.entries(settings.options)) {
        if (!known.has(key)) renderUnknown(parent, [...base, "settings", "options", key], key, option, context);
      }
    }
    renderDynamicProperties(parent, base, schema, value, new Set(Object.keys(schema.properties)), context);
    if (settingsValid) renderDynamicProperties(parent, [...base, "settings"], schema.properties.settings, settings, new Set(Object.keys(schema.properties.settings.properties)), context);
  }
}

function renderAddDialog(parent, packages, context) {
  const dialog = element("dialog", "ce-dialog");
  dialog.setAttribute("aria-label", "Add package");
  const form = element("form");
  const title = element("h2", "", "Add package");
  const id = element("input", "ce-input"); id.required = true; id.setAttribute("aria-label", "New package ID");
  const method = element("select", "ce-input"); method.setAttribute("aria-label", "New package sync type");
  for (const key of Object.keys(methodSchemas)) { const option = element("option", "", key); option.value = key; method.append(option); }
  let draft = createPackage("", method.value);
  const fields = element("div");
  const error = errorElement(); error.hidden = true;
  const draftContext = { ...context, onShowJson: null, onChange(path, next) {
    let target = draft;
    for (const [index, key] of path.slice(2, -1).entries()) { if (!hasOwn(target, key)) target[key] = typeof path[index + 3] === "number" ? [] : {}; target = target[key]; }
    const key = path.at(-1);
    const previous = target[key];
    const keepControls = hasOwn(target, key) && next !== null && typeof next !== "object" && typeof previous === typeof next;
    target[key] = next;
    if (!keepControls) renderDraft();
    else if (key === "src") {
      const repositoryPath = pathValue(path.slice(0, -1));
      const repository = [...fields.querySelectorAll(".ce-repository")].find((node) => node.dataset.path === repositoryPath);
      if (repository) repository.querySelector("summary").textContent = next || `Repository ${Number(path.at(-2)) + 1}`;
    }
  }, onRemove(path) {
    let target = draft; for (const key of path.slice(2, -1)) target = target[key];
    if (Array.isArray(target)) target.splice(path.at(-1), 1); else delete target[path.at(-1)];
    renderDraft();
  } };
  function renderDraft() {
    const focused = fields.contains(document.activeElement) ? document.activeElement : null;
    const focusedPath = focused?.dataset.path;
    const inputStates = new Map([...fields.querySelectorAll("input[data-path], select[data-path]")].map((node) => [
      `${node.tagName}:${node.type}:${JSON.stringify(JSON.parse(node.dataset.path).slice(2))}`,
      { value: node.value, checked: node.checked, invalid: !node.checkValidity() },
    ]));
    const disclosureStates = new Map([...fields.querySelectorAll("details")].map((node) => [node.dataset.path || node.dataset.section, node.open]));
    fields.replaceChildren();
    renderPackageFields(fields, draft.id, draft, draftContext);
    // The dialog's method selector owns method changes.
    for (const control of fields.querySelectorAll("[data-path]")) {
      if (JSON.parse(control.dataset.path).at(-1) === "synctype") control.closest(".ce-field")?.remove();
    }
    for (const details of fields.querySelectorAll("details")) {
      const key = details.dataset.path || details.dataset.section;
      if (disclosureStates.has(key)) details.open = disclosureStates.get(key);
    }
    if (!disclosureStates.has("method-options") && ["apt-mirror2", "jigdo", "ftpsync"].includes(method.value)) fields.querySelector(".ce-method-options").open = true;
    for (const node of fields.querySelectorAll("input[data-path], select[data-path]")) {
      const previous = inputStates.get(`${node.tagName}:${node.type}:${JSON.stringify(JSON.parse(node.dataset.path).slice(2))}`);
      if (previous?.invalid) { node.value = previous.value; if (node.type === "checkbox") node.checked = previous.checked; }
    }
    if (focusedPath) [...fields.querySelectorAll("input[data-path],select[data-path]")].find((node) => node.dataset.path === focusedPath)?.focus();
  }
  method.addEventListener("change", () => {
    const invalid = [...fields.querySelectorAll("input")].find((node) => !node.checkValidity());
    if (invalid) { method.value = draft.synctype; invalid.reportValidity(); return; }
    const next = createPackage(id.value.trim(), method.value);
    next.settings = { ...draft.settings, options: { ...next.settings.options, ...draft.settings.options } };
    draft = { ...draft, synctype: method.value, syncrate: next.syncrate, settings: next.settings };
    renderDraft();
  });
  id.addEventListener("change", () => {
    const next = id.value.trim();
    const previous = draft.id;
    if (draft.name === previous || draft.name === "New Package") draft.name = next;
    if (draft.href === (previous ? `/${previous}` : "")) draft.href = `/${next}`;
    if (draft.settings.dst === (previous ? `/srv/mirror/${previous}` : "")) draft.settings.dst = `/srv/mirror/${next}`;
    draft.id = next;
    renderDraft();
  });
  const submit = element("button", "ce-button", "Create package"); submit.type = "submit";
  const cancel = element("button", "ce-button", "Cancel"); cancel.type = "button";
  cancel.addEventListener("click", () => dialog.close());
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const key = id.value.trim(); draft.id = key;
    const issues = validateConfig({ packages: { [key]: draft } }).filter((issue) => issue.severity === "error" && issue.path[0] === "packages");
    if (!key || unsafeKeys.has(key) || hasOwn(packages, key)) issues.unshift({ message: "Enter a unique, safe package ID." });
    if (draft.synctype !== "local" && !draft.settings.dst.trim()) issues.unshift({ message: "Destination is required." });
    if (["rsync", "ftpsync", "lftp", "ubuntu", "debmirror"].includes(draft.synctype) && !draft.settings.src.trim()) issues.unshift({ message: "Source is required." });
    if (issues.length) { error.textContent = issues.map((issue) => issue.message).join(" "); error.hidden = false; return; }
    dialog.close(); context.onAddPackage(key, method.value, draft);
  });
  form.append(title, element("label", "", "Package ID"), id, element("label", "", "Sync method"), method, fields, error, submit, cancel);
  dialog.append(form); parent.append(dialog); renderDraft();
  return dialog;
}

function packageOptionLabel(key, value, issues) {
  const name = typeof value?.name === "string" ? value.name : "";
  const hasErrors = issues.some((issue) => issue.severity === "error" && issue.path[0] === "packages" && issue.path[1] === key);
  return `${hasErrors ? "⚠ " : ""}${key}${name && name !== key ? ` — ${name}` : ""}`;
}

export function refreshFormSummaries(container, value, issues = []) {
  const packages = value?.packages;
  if (packages && typeof packages === "object" && !Array.isArray(packages)) {
    for (const bar of container.querySelectorAll(".ce-package-bar")) {
      const state = packageBarStates.get(bar);
      if (state) { state.packages = packages; state.issues = issues; }
      for (const option of bar.querySelectorAll("select option")) {
        if (option.value && hasOwn(packages, option.value)) option.textContent = packageOptionLabel(option.value, packages[option.value], issues);
      }
    }
  }
  for (const repository of container.querySelectorAll(".ce-repository[data-path]")) {
    if (repository.closest("dialog")) continue;
    const path = JSON.parse(repository.dataset.path);
    let current = value;
    for (const key of path) current = hasOwn(current, key) ? current[key] : undefined;
    const summary = repository.querySelector("summary");
    if (summary) summary.textContent = typeof current?.src === "string" && current.src ? current.src : `Repository ${Number(path.at(-1)) + 1}`;
  }
}

function renderPackageBar(parent, packages, selectedPackage, context) {
  const bar = element("div", "ce-package-bar");
  const state = { packages, issues: context.issues };
  packageBarStates.set(bar, state);
  const search = element("input", "ce-input ce-search"); search.type = "search"; search.placeholder = "Find a package"; search.setAttribute("aria-label", "Search packages");
  const selectLabel = element("label", "", "Configuration section");
  const select = element("select", "ce-input"); select.id = controlId(["packages"]); selectLabel.htmlFor = select.id;
  function populate() {
    const query = search.value.toLocaleLowerCase();
    select.replaceChildren();
    const global = element("option", "", "Global settings"); global.value = ""; select.append(global);
    for (const [key, value] of Object.entries(state.packages)) {
      const name = typeof value?.name === "string" ? value.name : "";
      if (query && key !== selectedPackage && !`${key} ${name}`.toLocaleLowerCase().includes(query)) continue;
      const option = element("option", "", packageOptionLabel(key, value, state.issues)); option.value = key; select.append(option);
    }
    select.value = [...select.options].some((option) => option.value === selectedPackage) ? selectedPackage : "";
  }
  populate(); search.addEventListener("input", populate);
  select.addEventListener("change", () => context.onSelectPackage?.(select.value || null));
  const add = element("button", "ce-button", "+ Add package"); add.type = "button"; add.setAttribute("aria-label", "Add package");
  const dialog = renderAddDialog(parent, packages, context);
  add.addEventListener("click", () => dialog.showModal());
  dialog.addEventListener("close", () => { if (add.isConnected) add.focus(); });
  bar.append(search, selectLabel, select, add); parent.prepend(bar);
}

function renderSelectedPackage(parent, packages, packageId, context) {
  const value = packages[packageId];
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    renderMalformed(parent, ["packages", packageId], packageId, packageSchema, value, { ...context, removable: false }); return;
  }
  const section = element("section", "ce-section");
  const header = element("div", "ce-package-heading"); header.append(element("h2", "", `Package: ${packageId}`));
  const actions = element("details", "ce-package-actions"); actions.dataset.section = "package-actions";
  actions.append(element("summary", "", "Package actions"));
  const rename = element("input", "ce-input"); rename.value = packageId; rename.dataset.draftIgnore = "true"; rename.setAttribute("aria-label", "Package ID"); setPath(rename, ["packages", packageId, "id"]);
  const renameButton = element("button", "ce-button", "Rename package"); renameButton.type = "button";
  const error = errorElement(); error.hidden = true;
  renameButton.addEventListener("click", () => {
    const next = rename.value.trim();
    const reserved = validateConfig({ packages: { [next]: { id: next } } }).some((issue) => issue.path.length === 2 && issue.path[0] === "packages");
    if (!next || unsafeKeys.has(next) || reserved || (next !== packageId && hasOwn(packages, next))) { error.textContent = "Enter a unique, safe package ID."; error.hidden = false; return; }
    if (next !== packageId) context.onRename(packageId, next);
  });
  const remove = element("button", "ce-button", "Delete package"); remove.type = "button"; remove.addEventListener("click", () => context.onDeletePackage(packageId));
  actions.append(rename, renameButton, remove, error); header.append(actions); section.append(header);
  const tabs = element("div", "ce-tabs"); tabs.setAttribute("role", "tablist"); tabs.setAttribute("aria-label", "Package settings");
  const selected = Object.hasOwn(packageTabs, context.selectedTab) ? context.selectedTab : "sync";
  for (const [key, label] of Object.entries(packageTabs)) {
    const count = context.issues.filter((issue) => issue.severity === "error" && issue.path[0] === "packages" && issue.path[1] === packageId && packageTabForPath(issue.path) === key).length;
    const button = element("button", "ce-tab", `${label}${count ? ` (${count})` : ""}`); button.type = "button"; button.setAttribute("role", "tab"); button.setAttribute("aria-label", label);
    button.dataset.tab = key; button.dataset.baseLabel = label;
    button.id = `ce-package-tab-${key}`; button.setAttribute("aria-controls", "ce-package-panel"); button.setAttribute("aria-selected", String(selected === key)); button.tabIndex = selected === key ? 0 : -1;
    button.addEventListener("click", () => context.onSelectTab?.(key));
    button.addEventListener("keydown", (event) => {
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
      event.preventDefault(); const keys = Object.keys(packageTabs); const index = keys.indexOf(key);
      const next = keys[event.key === "Home" ? 0 : event.key === "End" ? keys.length - 1 : (index + (event.key === "ArrowRight" ? 1 : -1) + keys.length) % keys.length];
      context.onSelectTab?.(next); document.getElementById(`ce-package-tab-${next}`)?.focus();
    }); tabs.append(button);
  }
  section.append(tabs);
  const panel = element("div", "ce-package-panel"); panel.id = "ce-package-panel"; panel.setAttribute("role", "tabpanel"); panel.setAttribute("aria-labelledby", `ce-package-tab-${selected}`);
  renderPackageFields(panel, packageId, value, context, selected); section.append(panel); parent.append(section);
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
    ...callbacks,
    issues: callbacks.issues || [],
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
