from mcp.server.fastmcp import FastMCP, Context
import httpx
import os
from typing import Dict, Optional, Any
from dotenv import load_dotenv
import logging

# Load environment variables from .env file if available
# This allows fallback to .env but prioritizes environment variables
load_dotenv()

# Get Notion API token from environment variables
NOTION_API_KEY = os.getenv("NOTION_API_KEY")
NOTION_VERSION = "2022-06-28"  # Current Notion API version

# Validate API key is available
if not NOTION_API_KEY:
    print("WARNING: NOTION_API_KEY environment variable is not set.")
    print("Please provide it when registering this server with your MCP client.")

# Create MCP server
mcp = FastMCP("Notion Explorer", dependencies=["httpx", "python-dotenv"])
logger = logging.getLogger(__name__)

# Function to check API key on each request
def check_api_key(ctx: Context) -> None:
    """Check if API key is set before each request"""
    global NOTION_API_KEY
    # Get the latest environment version of the key (in case it was passed to the server)
    NOTION_API_KEY = os.getenv("NOTION_API_KEY")
    if not NOTION_API_KEY:
        ctx.error(
            "NOTION_API_KEY environment variable is not set. Please set it before making API calls."
        )
        raise ValueError("NOTION_API_KEY environment variable is not set")


# Base headers for Notion API requests
def get_headers():
    return {
        "Authorization": f"Bearer {NOTION_API_KEY}",
        "Content-Type": "application/json",
        "Notion-Version": NOTION_VERSION,
    }


@mcp.tool()
async def search_notion_pages(
    query: Optional[str] = None,
    filter_type: Optional[str] = None,
    page_size: int = 10,
    ctx: Context = None,
) -> str:
    # Check for API key
    check_api_key(ctx)
    """
    Search for pages and databases in Notion.
    
    Args:
        query: Optional search term to find specific pages/databases
        filter_type: Optional filter - can be "page" or "database" to limit results
        page_size: Number of results to return (max 100)
    
    Returns:
        A formatted string with search results
    """
    url = "https://api.notion.com/v1/search"

    # Build request payload
    payload = {}
    if query:
        payload["query"] = query

    if page_size:
        payload["page_size"] = min(100, page_size)  # Enforce max of 100

    if filter_type and filter_type in ["page", "database"]:
        payload["filter"] = {"value": filter_type, "property": "object"}

    # Sort by recently edited first
    payload["sort"] = {"direction": "descending", "timestamp": "last_edited_time"}
    print("payload", payload)
    async with httpx.AsyncClient() as client:
        response = await client.post(url, headers=get_headers(), json=payload)
        print(response.json())
        if response.status_code != 200:
            return f"Error: {response.status_code} - {response.text}"

        data = response.json()
        results = data.get("results", [])

        if not results:
            return "No results found"

        # Format the results
        output = []
        for item in results:
            item_id = item.get("id", "Unknown ID")
            item_type = item.get("object", "unknown")

            # Get title/name from appropriate property based on type
            title = "Untitled"
            if item_type == "page":
                # Handle both page formats (with properties or with just title)
                if "properties" in item and "title" in item["properties"]:
                    title_parts = item["properties"]["title"].get("title", [])
                    if title_parts:
                        title = "".join(
                            [part.get("plain_text", "") for part in title_parts]
                        )
                elif "title" in item:
                    title_parts = item["title"]
                    if title_parts:
                        title = "".join(
                            [part.get("plain_text", "") for part in title_parts]
                        )
            elif item_type == "database":
                if "title" in item:
                    title_parts = item["title"]
                    if title_parts:
                        title = "".join(
                            [part.get("plain_text", "") for part in title_parts]
                        )

            url = item.get("url", "No URL")
            last_edited = item.get("last_edited_time", "Unknown")

            output.append(
                f"- {title} ({item_type})\n  ID: {item_id}\n  URL: {url}\n  Last Edited: {last_edited}"
            )

        return "\n\n".join(output)


@mcp.tool()
async def get_page_content(page_id: str, ctx: Context = None) -> str:
    logger.info("get_page_content", page_id)
    # Check for API key
    check_api_key(ctx)
    """
    Get the content of a specific Notion page.
    
    Args:
        page_id: The ID of the Notion page to retrieve
    
    Returns:
        A formatted string with the page content
    """
    # First, get page metadata
    page_url = f"https://api.notion.com/v1/pages/{page_id}"

    async with httpx.AsyncClient() as client:
        # Get page metadata
        response = await client.get(page_url, headers=get_headers())

        if response.status_code != 200:
            return f"Error retrieving page: {response.status_code} - {response.text}"

        page_data = response.json()

        # Get page blocks (content)
        blocks_url = f"https://api.notion.com/v1/blocks/{page_id}/children"
        response = await client.get(blocks_url, headers=get_headers())

        if response.status_code != 200:
            return f"Error retrieving page content: {response.status_code} - {response.text}"

        blocks_data = response.json()

        # Format the page data
        output = []

        # Add page title/metadata
        page_title = "Untitled"
        if "properties" in page_data:
            title_prop = page_data["properties"].get("title") or page_data[
                "properties"
            ].get("Name")
            if title_prop and "title" in title_prop:
                title_parts = title_prop["title"]
                if title_parts:
                    page_title = "".join(
                        [part.get("plain_text", "") for part in title_parts]
                    )

        output.append(f"# {page_title}")
        output.append(f"Page ID: {page_data.get('id')}")
        output.append(f"URL: {page_data.get('url')}")
        output.append(f"Last Edited: {page_data.get('last_edited_time')}")
        output.append("\n## Content:\n")

        # Process blocks
        for block in blocks_data.get("results", []):
            block_content = await format_block(block)
            if block_content:
                output.append(block_content)

        return "\n".join(output)


async def format_block(block: Dict[str, Any], indent: int = 0) -> str:
    """Format a Notion block into readable text"""
    block_type = block.get("type")
    block_id = block.get("id")
    has_children = block.get("has_children", False)

    indent_str = "  " * indent
    result = []

    if not block_type or block_type not in block:
        return f"{indent_str}[Unsupported block type: {block_type}]"

    content = block[block_type]

    # Handle different block types
    if block_type == "paragraph":
        text = "".join(
            [
                text_item.get("plain_text", "")
                for text_item in content.get("rich_text", [])
            ]
        )
        result.append(f"{indent_str}{text}")

    elif block_type == "heading_1":
        text = "".join(
            [
                text_item.get("plain_text", "")
                for text_item in content.get("rich_text", [])
            ]
        )
        result.append(f"{indent_str}# {text}")

    elif block_type == "heading_2":
        text = "".join(
            [
                text_item.get("plain_text", "")
                for text_item in content.get("rich_text", [])
            ]
        )
        result.append(f"{indent_str}## {text}")

    elif block_type == "heading_3":
        text = "".join(
            [
                text_item.get("plain_text", "")
                for text_item in content.get("rich_text", [])
            ]
        )
        result.append(f"{indent_str}### {text}")

    elif block_type == "bulleted_list_item":
        text = "".join(
            [
                text_item.get("plain_text", "")
                for text_item in content.get("rich_text", [])
            ]
        )
        result.append(f"{indent_str}• {text}")

    elif block_type == "numbered_list_item":
        text = "".join(
            [
                text_item.get("plain_text", "")
                for text_item in content.get("rich_text", [])
            ]
        )
        result.append(
            f"{indent_str}1. {text}"
        )  # Simplified, won't have proper numbering

    elif block_type == "to_do":
        text = "".join(
            [
                text_item.get("plain_text", "")
                for text_item in content.get("rich_text", [])
            ]
        )
        checked = "✓" if content.get("checked", False) else "☐"
        result.append(f"{indent_str}{checked} {text}")

    elif block_type == "toggle":
        text = "".join(
            [
                text_item.get("plain_text", "")
                for text_item in content.get("rich_text", [])
            ]
        )
        result.append(f"{indent_str}▶ {text}")

    elif block_type == "code":
        text = "".join(
            [
                text_item.get("plain_text", "")
                for text_item in content.get("rich_text", [])
            ]
        )
        language = content.get("language", "")
        result.append(f"{indent_str}```{language}\n{indent_str}{text}\n{indent_str}```")

    elif block_type == "image":
        caption = "".join(
            [
                text_item.get("plain_text", "")
                for text_item in content.get("caption", [])
            ]
        )
        url = ""
        if "file" in content:
            url = content["file"].get("url", "")
        elif "external" in content:
            url = content["external"].get("url", "")

        caption_text = f" - {caption}" if caption else ""
        result.append(f"{indent_str}[Image{caption_text}]({url})")

    elif block_type == "divider":
        result.append(f"{indent_str}---")

    elif block_type == "callout":
        text = "".join(
            [
                text_item.get("plain_text", "")
                for text_item in content.get("rich_text", [])
            ]
        )
        emoji = content.get("icon", {}).get("emoji", "")
        result.append(f"{indent_str}{emoji} | {text}")

    elif block_type == "quote":
        text = "".join(
            [
                text_item.get("plain_text", "")
                for text_item in content.get("rich_text", [])
            ]
        )
        result.append(f"{indent_str}> {text}")

    elif block_type == "table":
        result.append(f"{indent_str}[Table - use get_table_content to view]")

    else:
        result.append(f"{indent_str}[{block_type} block]")

    # If the block has children, we'd need to make another API call to get them
    if has_children:
        result.append(
            f"{indent_str}[This block has child blocks that aren't displayed here]"
        )

    return "\n".join(result)


@mcp.tool()
async def get_database_content(
    database_id: str, max_pages: int = 10, ctx: Context = None
) -> str:
    # Check for API key
    check_api_key(ctx)
    """
    Get the content of a Notion database.
    
    Args:
        database_id: The ID of the Notion database to retrieve
        max_pages: Maximum number of pages to return from the database
    
    Returns:
        A formatted string with the database structure and entries
    """
    database_url = f"https://api.notion.com/v1/databases/{database_id}"
    query_url = f"https://api.notion.com/v1/databases/{database_id}/query"

    async with httpx.AsyncClient() as client:
        # Get database metadata
        response = await client.get(database_url, headers=get_headers())

        if response.status_code != 200:
            return (
                f"Error retrieving database: {response.status_code} - {response.text}"
            )

        db_data = response.json()

        # Query database entries
        payload = {
            "page_size": min(max_pages, 100)  # Maximum 100 per request
        }

        response = await client.post(query_url, headers=get_headers(), json=payload)

        if response.status_code != 200:
            return f"Error querying database: {response.status_code} - {response.text}"

        query_data = response.json()

        # Format the database data
        output = []

        # Add database title/metadata
        db_title = "Untitled Database"
        if "title" in db_data:
            title_parts = db_data["title"]
            if title_parts:
                db_title = "".join([part.get("plain_text", "") for part in title_parts])

        output.append(f"# {db_title}")
        output.append(f"Database ID: {db_data.get('id')}")
        output.append(f"URL: {db_data.get('url')}")

        # Add database properties/schema
        output.append("\n## Database Schema:")
        for prop_name, prop_data in db_data.get("properties", {}).items():
            prop_type = prop_data.get("type", "unknown")
            output.append(f"- {prop_name}: {prop_type}")

        # Add database entries
        entries = query_data.get("results", [])
        output.append(f"\n## Database Entries ({len(entries)}):")

        for i, entry in enumerate(entries, 1):
            output.append(f"\n### Entry {i}")
            output.append(f"ID: {entry.get('id')}")
            output.append(f"URL: {entry.get('url')}")

            # Extract and format properties
            properties = entry.get("properties", {})
            for prop_name, prop_data in properties.items():
                prop_type = prop_data.get("type", "unknown")

                # Extract property value based on type
                prop_value = "N/A"
                if prop_type == "title" and "title" in prop_data:
                    title_parts = prop_data["title"]
                    prop_value = "".join(
                        [part.get("plain_text", "") for part in title_parts]
                    )
                elif prop_type == "rich_text" and "rich_text" in prop_data:
                    text_parts = prop_data["rich_text"]
                    prop_value = "".join(
                        [part.get("plain_text", "") for part in text_parts]
                    )
                elif prop_type == "number" and "number" in prop_data:
                    prop_value = prop_data["number"]
                elif (
                    prop_type == "select"
                    and "select" in prop_data
                    and prop_data["select"]
                ):
                    prop_value = prop_data["select"].get("name", "")
                elif prop_type == "multi_select" and "multi_select" in prop_data:
                    options = prop_data["multi_select"]
                    prop_value = ", ".join(
                        [option.get("name", "") for option in options]
                    )
                elif prop_type == "date" and "date" in prop_data and prop_data["date"]:
                    date_obj = prop_data["date"]
                    start = date_obj.get("start", "")
                    end = date_obj.get("end", "")
                    prop_value = start
                    if end:
                        prop_value += f" to {end}"
                elif prop_type == "checkbox" and "checkbox" in prop_data:
                    prop_value = "✓" if prop_data["checkbox"] else "☐"
                elif prop_type == "url" and "url" in prop_data:
                    prop_value = prop_data["url"] or "N/A"
                elif prop_type == "email" and "email" in prop_data:
                    prop_value = prop_data["email"] or "N/A"
                elif prop_type == "phone_number" and "phone_number" in prop_data:
                    prop_value = prop_data["phone_number"] or "N/A"

                output.append(f"- {prop_name}: {prop_value}")

        return "\n".join(output)


@mcp.tool()
async def get_block_children(block_id: str, ctx: Context = None) -> str:
    # Check for API key
    check_api_key(ctx)
    """
    Get the child blocks of a specific Notion block.
    
    Args:
        block_id: The ID of the Notion block whose children to retrieve
    
    Returns:
        A formatted string with the block's children content
    """
    url = f"https://api.notion.com/v1/blocks/{block_id}/children"

    async with httpx.AsyncClient() as client:
        response = await client.get(url, headers=get_headers())

        if response.status_code != 200:
            return f"Error retrieving block children: {response.status_code} - {response.text}"

        data = response.json()
        results = data.get("results", [])

        if not results:
            return "This block has no children."

        # Format the results
        output = []
        for block in results:
            block_content = await format_block(block)
            if block_content:
                output.append(block_content)

        return "\n".join(output)


if __name__ == "__main__":
    # TODO: logging is not working
    # TODO: running it with `python main.py` doesn't work, get's stuck, neither with `uv run main.py`
    # only runs when called from the client, either the inspector or claude desktop
    mcp.run()
