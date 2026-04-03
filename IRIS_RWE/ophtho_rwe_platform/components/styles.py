"""
Shared styling — injects platform-wide CSS with 3D/depth effects.
Supports both light and dark mode via prefers-color-scheme media query
and Streamlit's [data-theme] attribute.

Call inject_styles() at the top of every page (after set_page_config).
"""

import streamlit as st
_CSS = ""

def inject_styles() -> None:
    pass
