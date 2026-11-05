"""MCP tools served by the editor's embedded local HTTP server."""

import asyncio

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from gst_creator.control_schema import Edit


INSTRUCTIONS = """
Control the running GStreamer Pipeline Creator app. Turn a user's description
into real elements and pad connections on its canvas, then refine that graph.
First read get_pipeline. Search and inspect installed elements; never assume a
plugin, pad or property exists. Discovery must be ready before editing.
Use apply_pipeline_edits to create or modify the graph. It is atomic and one
undo step. Supply the revision most recently read, and refer to elements by
their ID or unique instance name. Elements added earlier in a batch can be
referenced by their name. Keep existing work unless the user asks to replace
it; clear is an explicit operation. Append auto_layout after building a graph.
Properties use natural values, including plain strings, true/false, numbers,
enum nicknames, flags nicknames joined by +, and null to reset a property.
Use pad TEMPLATE names from inspect_element (e.g. src_%u); request pad slots
are assigned automatically. To insert an element, disconnect the old link,
add the element, and connect both sides in the same batch. Use connection IDs
from get_pipeline when disconnecting or changing caps.
Read returned validation issues. Static validation cannot prove runtime caps
negotiation or device/file availability. Dynamic pad names may be unsupported.
No tool runs pipelines, arbitrary code, shell commands, or file operations.
undo_pipeline_edit and redo_pipeline_edit operate on the shared editor history,
including the user's manual edits. Only use them when the user asks to undo or
redo. On a revision conflict or timeout, read the graph before retrying.
"""

READ_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
EDITING = ToolAnnotations(readOnlyHint=False, destructiveHint=True,
                         openWorldHint=False)


def create_mcp(dispatch):
    """Create an independent server whose tools dispatch to one editor."""
    mcp = FastMCP(
        "gst-creator", instructions=INSTRUCTIONS, log_level="WARNING",
        host="127.0.0.1", stateless_http=True, json_response=True,
    )

    @mcp.tool(annotations=READ_ONLY)
    async def get_pipeline() -> dict:
        """Read elements, properties, connections, revision and app status."""
        return await asyncio.to_thread(dispatch, "get_pipeline", {})

    @mcp.tool(annotations=READ_ONLY)
    async def search_elements(
        query: str = "", offset: int = 0, limit: int = 30,
    ) -> dict:
        """Search installed factories, plugins, categories and descriptions.

        All space-separated query terms must match. Results are paginated;
        limit is 1..100. An empty query lists all installed factories.
        """
        return await asyncio.to_thread(dispatch, "search_elements", {
            "query": query, "offset": offset, "limit": limit,
        })

    @mcp.tool(annotations=READ_ONLY)
    async def inspect_element(factory: str) -> dict:
        """Get factory metadata, pad templates, caps and property choices."""
        return await asyncio.to_thread(
            dispatch, "inspect_element", {"factory": factory},
        )

    @mcp.tool(annotations=EDITING)
    async def apply_pipeline_edits(
        operations: list[Edit], expected_revision: str,
    ) -> dict:
        """Apply an ordered batch of edits to the canvas as one undo step.

        Pass the latest get_pipeline revision. All operations succeed together
        or none are applied. Use unique names for newly added elements and
        their names or IDs in later operations. null resets a property.
        remove_element also removes its connections. clear removes everything.
        auto_layout arranges sources to sinks. Returns graph and validation.
        """
        return await asyncio.to_thread(dispatch, "apply_pipeline_edits", {
            "operations": [
                operation.model_dump() for operation in operations
            ],
            "expected_revision": expected_revision,
        })

    @mcp.tool(annotations=READ_ONLY)
    async def validate_pipeline() -> dict:
        """Get static issues, a POSIX gst-launch command and argument list.

        Does not run pipelines or check devices, files or runtime negotiation.
        """
        return await asyncio.to_thread(dispatch, "validate_pipeline", {})

    @mcp.tool(annotations=EDITING)
    async def undo_pipeline_edit(expected_revision: str) -> dict:
        """Undo the latest shared editor edit, which may be a manual edit."""
        return await asyncio.to_thread(dispatch, "undo_pipeline_edit", {
            "expected_revision": expected_revision,
        })

    @mcp.tool(annotations=EDITING)
    async def redo_pipeline_edit(expected_revision: str) -> dict:
        """Redo the latest undone shared editor edit."""
        return await asyncio.to_thread(dispatch, "redo_pipeline_edit", {
            "expected_revision": expected_revision,
        })

    return mcp
