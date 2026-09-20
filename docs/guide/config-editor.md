# Config editor

Edit `config.json` directly on the left or use the settings form on the right.
Both views update the same document. Open a local file or start with a new
configuration, then download the result for installation on your mirror host.

```{raw} html
<div id="mirror-config-editor">
  <p>Loading the configuration editor. JavaScript and an HTTP(S) connection are required.</p>
  <noscript>Enable JavaScript to use the editor. You can also use the configuration reference to edit your file manually.</noscript>
</div>
```

## Editing and validation

- Search packages by ID or display name, then edit their source, destination,
  method, and interval in **Sync**. Use **Basic information**, **Links**, and
  **Advanced** for other settings. Selecting a package reveals its JSON location.
- **Add package** opens a separate dialog. **Package actions** contains rename
  and delete. Interval inputs accept whole days, hours, minutes, or seconds;
  **Edit ISO 8601** exposes the original syntax for custom intervals and `PUSH`.
- Use **Settings only** for more editing space, or hide the documentation
  navigation. A field's **JSON** button reveals its source without changing it.
- Inputs are committed when you leave the field or press Enter. An invalid input
  blocks switching views and downloading until you fix it or choose **Discard
  input** (Escape). If JSON also changes that value, choose explicitly which
  input to keep. During text composition, finish the composition before moving on.
- Optional fields stay absent until you add them. Removing a field restores the
  runtime default; it is different from setting an empty string or `false`.
- Switching sync methods keeps existing options. Review any reported mismatch
  before downloading. Unknown fields and external plugin options remain in the
  JSON and can be edited there.
- Syntax errors, duplicate keys, and invalid settings block configuration
  downloads. During a syntax error, the form keeps its last valid view but is
  disabled. **Copy JSON** remains available to save an unfinished draft.
- Form edits participate in the JSON editor's undo history. **Copy JSON** copies
  the committed document, excluding unfinished form input. Field changes preserve
  unrelated JSON text; **Format JSON** explicitly reformats the document.
- Validation checks the configuration structure and local option rules. It does
  not contact upstream repositories or check files, permissions, binaries, or
  signing keys on your server.

Files remain in this tab's memory. There is no upload, automatic storage, or
connection to the daemon. Download your changes before closing the page.
Per-plugin configuration files are separate and are not edited here.

The editor targets desktop browsers. Narrow windows open on Settings; use the JSON
and Settings tabs to switch panes. For configuration details, see the
[configuration reference](configuration.md) and [sync methods](../sync-methods/index.md).
