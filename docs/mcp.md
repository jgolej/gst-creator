# MCP integration

GStreamer Pipeline Creator provides a Model Context Protocol (MCP) server
for inspecting and editing pipelines through a compatible assistant or MCP
client. The server runs inside the application.

## Connection settings

| Setting | Value |
| --- | --- |
| Transport | Streamable HTTP |
| Default URL | `http://127.0.0.1:8765/mcp` |
| Authentication | None |
| Access | Local connections only |

## Setup

1. Start GStreamer Pipeline Creator as described in the
   [README](../README.md#running-the-application).
2. Check that **Tools > Enable AI Control** is enabled. It is enabled by
   default when the application starts.
3. Copy the MCP URL by clicking it in the application's status bar.
4. Add a Streamable HTTP server in the MCP client using that URL.

Wait for element discovery to finish before requesting pipeline edits.
The same connection settings apply to the executable and to the application
running from source.

The application must remain open while the client is connected. Disabling
AI control or closing the application stops the MCP server.

### Example: Codex

Add the following entry to the Codex `config.toml` file:

```toml
[mcp_servers.gst-creator]
url = "http://127.0.0.1:8765/mcp"
```

Alternatively, register the server with the Codex CLI:

```text
codex mcp add gst-creator --url http://127.0.0.1:8765/mcp
```

Restart the client if needed to load the configuration. See the
[Codex MCP documentation](https://learn.chatgpt.com/docs/extend/mcp)
for client-specific settings.

### Changing the port

Set the `GST_CREATOR_MCP_PORT` environment variable before starting the
application to use a different port. For example, a value of `8766` changes
the endpoint to `http://127.0.0.1:8766/mcp`.

Update the client configuration to match the URL shown in the status bar.
Each application instance needs its own port. If the port is already in
use, AI control remains disabled and an error appears in the status bar.

## Usage

Describe the pipeline or change to the connected assistant. For example:

- "Create a video test source, convert its video, and display it."
- "Crop 20 pixels from each edge and flip the video horizontally."
- "Explain the validation issues and fix the pipeline."

The assistant can discover installed elements, inspect their properties,
and edit the canvas. Changes appear immediately. Each successful edit batch
is one undo step in the application's shared history.

Save projects and start or stop pipelines through the application. Edits
do not change a pipeline that is already running; stop it and run it again
to apply the changes.

## Available tools

| Tool | Purpose |
| --- | --- |
| `get_pipeline` | Read the graph, revision, discovery status, and history. |
| `search_elements` | Search installed elements with pagination. |
| `inspect_element` | Read pads, caps, properties, and allowed values. |
| `apply_pipeline_edits` | Apply a batch of edits and return validation. |
| `validate_pipeline` | Read static issues and the generated command. |
| `undo_pipeline_edit` | Undo the latest shared editor history entry. |
| `redo_pipeline_edit` | Redo the latest undone history entry. |

### Editing workflow

Read `get_pipeline` before editing. Use `search_elements` and
`inspect_element` to check which elements, pads, and properties are
available on the current system.

Editing tools require `expected_revision` from the most recent pipeline
state. If the graph changes before an edit is applied, read the state again
and prepare a new request. After a timeout, read the graph before retrying:
the edit may already have been applied.

`apply_pipeline_edits` supports adding, removing, renaming, and moving
elements; editing properties and connection caps; connecting and
disconnecting pads; clearing the graph; and automatic layout. A batch is
applied as a whole. If an operation or static validation fails, the batch
is rejected without changing the pipeline.

Reference elements by their ID or unique instance name. Use pad template
names returned by inspection and connection IDs returned by `get_pipeline`.
Property values may be strings, booleans, or numbers. Use `null` to reset a
property and enum nicknames to select enum values.

## Limitations

- The server binds to `127.0.0.1` and cannot be accessed from another PC.
  It has no authentication; other local processes can connect.
- MCP tools edit and inspect pipelines. They do not run pipelines, save
  files, or execute arbitrary code or shell commands.
- Validation checks the graph but cannot verify devices, files, or runtime
  format negotiation. Pads whose names are only known at runtime may prevent
  command generation.
- Edits are rejected while an application dialog or mouse gesture is active.
- Undo and redo use the same history as manual edits and may affect changes
  made directly in the application.

## Troubleshooting

If the client cannot connect, check that the application is open, AI control
is enabled, and the client URL matches the status bar. The client must run
in an environment that can reach the application's local loopback address.

If element searches or edits report that discovery is not ready, wait for
discovery to finish. Check the Element Browser for errors and use
**Tools > Rediscover Elements** after changing installed plugins.
