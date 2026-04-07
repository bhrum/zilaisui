"""
WeChat Content Formatter
Converts Markdown to WeChat-compatible HTML with inline styles.

WeChat's rich text editor has strict limitations:
- No external CSS/stylesheets allowed
- All styles must be inline
- Limited HTML tag support
- No <script>, <link>, <style> tags
"""

import html
import re


def markdown_to_wechat_html(markdown_text: str) -> str:
    """
    Convert Markdown text to WeChat-compatible HTML with inline styles.

    This is a self-contained converter that doesn't require the `markdown` library,
    handling the most common Markdown patterns with WeChat-friendly styling.

    Args:
        markdown_text: Raw Markdown content.

    Returns:
        HTML string suitable for WeChat's editor.
    """
    lines = markdown_text.split("\n")
    html_parts: list[str] = []
    in_code_block = False
    code_block_lang = ""
    code_block_lines: list[str] = []
    in_list = False
    list_type = ""  # "ul" or "ol"

    for line in lines:
        # --- Code block handling ---
        if line.strip().startswith("```"):
            if in_code_block:
                # Close code block
                code_content = "\n".join(code_block_lines)
                html_parts.append(_format_code_block(code_content, code_block_lang))
                code_block_lines = []
                code_block_lang = ""
                in_code_block = False
            else:
                # Close any open list
                if in_list:
                    html_parts.append(f"</{list_type}>")
                    in_list = False
                # Open code block
                in_code_block = True
                lang_match = re.match(r"```(\w*)", line.strip())
                code_block_lang = lang_match.group(1) if lang_match else ""
            continue

        if in_code_block:
            code_block_lines.append(line)
            continue

        stripped = line.strip()

        # --- Empty line ---
        if not stripped:
            if in_list:
                html_parts.append(f"</{list_type}>")
                in_list = False
            html_parts.append("")
            continue

        # --- Headings ---
        heading_match = re.match(r"^(#{1,6})\s+(.+)$", stripped)
        if heading_match:
            if in_list:
                html_parts.append(f"</{list_type}>")
                in_list = False
            level = len(heading_match.group(1))
            text = _inline_format(heading_match.group(2))
            html_parts.append(_format_heading(text, level))
            continue

        # --- Horizontal rule ---
        if re.match(r"^[-*_]{3,}$", stripped):
            if in_list:
                html_parts.append(f"</{list_type}>")
                in_list = False
            html_parts.append(_format_hr())
            continue

        # --- Blockquote ---
        if stripped.startswith(">"):
            if in_list:
                html_parts.append(f"</{list_type}>")
                in_list = False
            quote_text = _inline_format(stripped.lstrip("> ").strip())
            html_parts.append(_format_blockquote(quote_text))
            continue

        # --- Unordered list ---
        ul_match = re.match(r"^[-*+]\s+(.+)$", stripped)
        if ul_match:
            if not in_list or list_type != "ul":
                if in_list:
                    html_parts.append(f"</{list_type}>")
                html_parts.append('<ul style="margin: 8px 0; padding-left: 2em;">')
                in_list = True
                list_type = "ul"
            item_text = _inline_format(ul_match.group(1))
            html_parts.append(
                f'<li style="margin: 4px 0; line-height: 1.8; '
                f'color: #3f3f3f; font-size: 15px;">{item_text}</li>'
            )
            continue

        # --- Ordered list ---
        ol_match = re.match(r"^(\d+)\.\s+(.+)$", stripped)
        if ol_match:
            if not in_list or list_type != "ol":
                if in_list:
                    html_parts.append(f"</{list_type}>")
                html_parts.append('<ol style="margin: 8px 0; padding-left: 2em;">')
                in_list = True
                list_type = "ol"
            item_text = _inline_format(ol_match.group(1))
            html_parts.append(
                f'<li style="margin: 4px 0; line-height: 1.8; '
                f'color: #3f3f3f; font-size: 15px;">{item_text}</li>'
            )
            continue

        # --- Image ---
        img_match = re.match(r"^!\[([^\]]*)\]\(([^)]+)\)$", stripped)
        if img_match:
            if in_list:
                html_parts.append(f"</{list_type}>")
                in_list = False
            alt = html.escape(img_match.group(1))
            src = img_match.group(2)
            html_parts.append(
                f'<p style="text-align: center; margin: 16px 0;">'
                f'<img src="{src}" alt="{alt}" '
                f'style="max-width: 100%; height: auto; border-radius: 4px;" />'
                f"</p>"
            )
            continue

        # --- Regular paragraph ---
        if in_list:
            html_parts.append(f"</{list_type}>")
            in_list = False
        text = _inline_format(stripped)
        html_parts.append(_format_paragraph(text))

    # Close any remaining open elements
    if in_code_block:
        code_content = "\n".join(code_block_lines)
        html_parts.append(_format_code_block(code_content, code_block_lang))
    if in_list:
        html_parts.append(f"</{list_type}>")

    return "\n".join(html_parts)


def _inline_format(text: str) -> str:
    """Apply inline Markdown formatting (bold, italic, code, links)."""
    # Bold + Italic
    text = re.sub(
        r"\*\*\*(.+?)\*\*\*",
        r'<strong style="color: #1a1a1a;"><em>\1</em></strong>',
        text,
    )
    # Bold
    text = re.sub(
        r"\*\*(.+?)\*\*",
        r'<strong style="color: #1a1a1a;">\1</strong>',
        text,
    )
    # Italic
    text = re.sub(r"\*(.+?)\*", r"<em>\1</em>", text)
    # Inline code
    text = re.sub(
        r"`([^`]+)`",
        r'<code style="background-color: #f5f5f5; padding: 2px 6px; '
        r'border-radius: 3px; font-size: 14px; color: #c7254e; '
        r'font-family: Consolas, Monaco, monospace;">\1</code>',
        text,
    )
    # Links
    text = re.sub(
        r"\[([^\]]+)\]\(([^)]+)\)",
        r'<a href="\2" style="color: #576b95; text-decoration: none;">\1</a>',
        text,
    )
    # Strikethrough
    text = re.sub(
        r"~~(.+?)~~",
        r'<del style="color: #999;">\1</del>',
        text,
    )
    return text


def _format_heading(text: str, level: int) -> str:
    """Format a heading with WeChat-compatible inline styles."""
    styles = {
        1: "font-size: 22px; font-weight: bold; color: #1a1a1a; margin: 24px 0 16px; padding-bottom: 8px; border-bottom: 1px solid #eee;",
        2: "font-size: 20px; font-weight: bold; color: #1a1a1a; margin: 20px 0 12px;",
        3: "font-size: 18px; font-weight: bold; color: #1a1a1a; margin: 16px 0 10px;",
        4: "font-size: 16px; font-weight: bold; color: #1a1a1a; margin: 14px 0 8px;",
        5: "font-size: 15px; font-weight: bold; color: #1a1a1a; margin: 12px 0 6px;",
        6: "font-size: 14px; font-weight: bold; color: #666; margin: 10px 0 6px;",
    }
    style = styles.get(level, styles[6])
    return f'<h{level} style="{style}">{text}</h{level}>'


def _format_paragraph(text: str) -> str:
    """Format a paragraph with WeChat-compatible inline styles."""
    return (
        f'<p style="margin: 10px 0; line-height: 1.8; '
        f'color: #3f3f3f; font-size: 15px; letter-spacing: 0.5px;">'
        f"{text}</p>"
    )


def _format_code_block(code: str, lang: str = "") -> str:
    """Format a code block with WeChat-compatible inline styles."""
    escaped = html.escape(code)
    return (
        f'<pre style="background-color: #2b2b2b; color: #a9b7c6; '
        f"padding: 16px; border-radius: 6px; overflow-x: auto; "
        f"font-size: 13px; line-height: 1.6; margin: 12px 0; "
        f'font-family: Consolas, Monaco, &quot;Courier New&quot;, monospace;">'
        f"<code>{escaped}</code></pre>"
    )


def _format_blockquote(text: str) -> str:
    """Format a blockquote with WeChat-compatible inline styles."""
    return (
        f'<blockquote style="margin: 12px 0; padding: 12px 16px; '
        f"border-left: 4px solid #576b95; background-color: #f7f7f7; "
        f'color: #666; font-size: 14px; line-height: 1.8;">'
        f"{text}</blockquote>"
    )


def _format_hr() -> str:
    """Format a horizontal rule."""
    return (
        '<hr style="border: none; border-top: 1px solid #ddd; '
        'margin: 20px 0;" />'
    )


def extract_digest(markdown_text: str, max_length: int = 54) -> str:
    """
    Extract article digest from Markdown text.
    Strips all formatting and takes the first `max_length` characters.

    Args:
        markdown_text: Raw Markdown content.
        max_length: Maximum characters for the digest.

    Returns:
        Plain text digest string.
    """
    # Remove headings
    text = re.sub(r"^#{1,6}\s+", "", markdown_text, flags=re.MULTILINE)
    # Remove code blocks
    text = re.sub(r"```[\s\S]*?```", "", text)
    # Remove inline code
    text = re.sub(r"`[^`]+`", "", text)
    # Remove images
    text = re.sub(r"!\[([^\]]*)\]\([^)]+\)", r"\1", text)
    # Remove links (keep text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    # Remove formatting markers
    text = re.sub(r"[*_~`>]", "", text)
    # Remove horizontal rules
    text = re.sub(r"^[-*_]{3,}$", "", text, flags=re.MULTILINE)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()

    if len(text) > max_length:
        return text[:max_length] + "..."
    return text
