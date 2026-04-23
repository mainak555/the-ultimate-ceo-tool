---
name: markdown-viewer-reuse
description: Require shared markdown rendering through one reusable module for Home, export popups, and future providers.
---

# Skill: Markdown Viewer Reuse

## Purpose
Avoid duplicated markdown parsing logic and keep rendering behavior consistent across chat history, export popups, and future provider surfaces.

## Required Rules
1. Reuse `server/static/server/js/markdown_viewer.js` for markdown-to-HTML conversion.
2. Do not implement ad-hoc markdown parsers inside feature modules when shared renderer is available.
3. Keep shared renderer in common layer and feature modules as callers only.
4. Preserve source-content rules: raw discussion markdown remains source of truth for reference panes.

## Integration Contract
1. Shared API: `window.MarkdownViewer.render(markdownText)`.
2. Home surfaces use shared renderer for user and assistant bubble markdown.
3. Export modal reference panes use shared renderer for raw markdown preview.
4. Project Config readonly surfaces use shared renderer for objective and prompt fields.
5. Future providers should plug into the same shared API.

## Review Checklist
1. Confirm no duplicated markdown parser logic remains in feature modules.
2. Confirm table/list/code-block rendering is consistent across Home and export modals.
3. Confirm Project Config readonly objective/system prompt/extraction prompt blocks hydrate via markdown targets and `window.MarkdownViewer.render()`.
4. Confirm fallback behavior remains safe when markdown library is unavailable.
