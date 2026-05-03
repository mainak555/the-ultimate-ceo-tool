"""MCP OAuth 2.0 URL routes."""

from django.urls import path
from . import mcp_views

urlpatterns = [
    # Single entry point for both phases: ?flow=test|run&server_name=...&...
    path("oauth/start/", mcp_views.mcp_oauth_start, name="mcp_oauth_start"),

    # Provider redirect-back callback: exchange code, render shared outcome page
    path("oauth/callback/", mcp_views.mcp_oauth_callback, name="mcp_oauth_callback"),
]
